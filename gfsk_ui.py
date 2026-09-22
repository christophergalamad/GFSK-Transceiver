#!/usr/bin/env python3
"""GALAMAD UHF GFSK Transceiver web UI.

Transmit: edit the message -> regenerate the GFSK IQ -> (re)start the HackRF
looping. Receive: capture via RTL-SDR (~6 s) -> GFSK demod/decoder -> show
payloads. Two visible phases: "Done capturing" then "now decoding".

Run:  python gfsk_ui.py   -> open http://localhost:5000/
"""

import os
import re
import subprocess
import sys
import threading
import time

import serial
from flask import Flask, jsonify, render_template_string, request

import config

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
DATA = os.path.join(ROOT, "data")
LOGS = os.path.join(ROOT, "logs")
sys.path.insert(0, SRC)

import decode_sst01 as dec

CARRIER_HZ = config.CENTER_FREQ_HZ
TX_RATE = config.TX_SAMPLE_RATE
IQ_FILE = os.path.join(DATA, "live.iq")
RX_FILE = os.path.join(DATA, "rx_live.iq")
DEFAULT_MSG = config.DEFAULT_MESSAGE
GEN_PY = os.path.join(SRC, "generate_gfsk_iq.py")
PORT = int(os.environ.get("PORT", "5000"))

# Link presets: rate/dev only — frequency is independent (controlled by freq box)
# 'bench' decodes reliably on a noisy bench (narrow 9.6k);
# 'sat' matches the GALAMAD satellite 200 kbps PHY (needs a clean RF link).
PRESETS = {
    "bench": {"label": "Bench 9.6 kbps",
              "rate": 9600, "dev": 4800, "freq": 437_080_000,
              "rx_rate": 1_200_000, "cutoff": 25_000,
              "digital_gain": 0.5, "tx_rate": 2_400_000},
    "sat": {"label": "Satellite 200 kbps",
            "rate": 200_000, "dev": 50_000, "freq": 437_000_000,
            "rx_rate": 2_400_000, "cutoff": 150_000,
            "digital_gain": 0.5, "tx_rate": 2_400_000},
}
_cur = {"name": "bench"}      # default: reliable bench decode
_link = {"freq": 437_000_000, "rx_freq": 437_000_000}   # TX + RX carriers (Hz), independently set via the two freq boxes


def _preset():
    return PRESETS[_cur["name"]]


def _link_freq():
    return _link["freq"]


def _rx_freq():
    """The frequency the user wants to receive (the peer TX carrier)."""
    return _link["rx_freq"]


def _rx_tune():
    return _rx_freq() - 100_000           # carrier lands at +100 kHz baseband


def _rx_rate():
    """RX sample rate. HackRF must be >= 2 MHz (RTL-SDR can use the preset)."""
    rate = _preset()["rx_rate"]
    if _sel["rx_kind"] == "hackrf" and rate < 2_000_000:
        rate = 2_400_000
    return rate


def _rx_samples():
    return int(_rx_rate() * config.RX_CAPTURE_SECONDS)


os.makedirs(DATA, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)

app = Flask(__name__)

_lock = threading.Lock()          # guards TX process
_cap_lock = threading.Lock()      # guards capture/decode file mutex
_cap_proc = {"proc": None}        # current RX subprocess (for preemption)
_rtl_cache = []                   # last-known RTL-SDR index list
_tx = {"proc": None, "msg": None}
_sel = {"rx_kind": "rtl", "rx_index": 0, "hackrf_serial": None,
        "hackrf_rx_serial": None, "rx_lna": 24, "rx_vga": 48}
_tx_cfg = {"tx_gain": config.TX_ATTENUATION,
           "amp": bool(config.TX_AMPLIFIER)}             # HackRF TX power/amp

# ---- SST01 / Nucleo serial control ----
_SST01_PORT = os.environ.get("SST01_PORT", "/dev/ttyACM0")
_SST01_BAUD = 115200
_ACTIVITY_AGE = 8.0            # how long a firmware-reported tx/rx stays "current"
_sst = {"serial": None, "lock": threading.Lock(),
        "mode": None, "rx_line": "", "rx_msgs": [], "tx_msgs": [], "last_state": "",
        "present": False, "error": None,
        "auto": None,          # None=unknown, True=autonomous TX/RX firmware, False=command firmware
        "activity": None,      # 'tx'|'rx' in the current window
        "activity_ts": 0.0, "_linebuf": "",
        "rxwin_seen": False,   # alternator prints 'rxwin got=' → it is AUTO RX/TX
        "ph_tx": False,        # autonomous-firmware TX evidence seen
        "ph_rx": False}        # autonomous-firmware RX evidence seen


def _ts():
    return time.strftime("%H:%M:%S")


def _sst_open():
    if _sst["serial"] is not None:
        return True
    try:
        s = serial.Serial(_SST01_PORT, _SST01_BAUD, timeout=0.2)
        s.reset_input_buffer()
        time.sleep(1.0)            # let Nucleo boot (POR banner)
        s.reset_input_buffer()
        _sst["serial"] = s
        _sst["present"] = True
        _sst["error"] = None
        return True
    except Exception as e:
        _sst["error"] = f"cannot open {_SST01_PORT}: {e}"
        return False


def _sst_cmd(cmd, wait=0.4):
    """Send one command line, return concatenated serial response."""
    with _sst["lock"]:
        s = _sst["serial"]
        if s is None:
            return _sst["error"] or "not connected"
        try:
            s.write((cmd + "\n").encode())
            time.sleep(wait)
            buf = b""
            deadline = time.time() + wait + 0.5
            while time.time() < deadline:
                chunk = s.read(512)
                if not chunk:
                    break
                buf += chunk
            resp = buf.decode("ascii", errors="replace")
            line = resp.strip()
            if line:
                _sst["last_state"] = line.splitlines()[-1]
            return line
        except Exception as e:
            _sst["present"] = False
            _sst["serial"] = None
            _sst["error"] = f"serial error: {e}"
            return _sst["error"]


def _sst_ingest(line):
    """Infer real radio state from whatever firmware is running.

    Command firmware (sst_rx_tx.ino) echoes: PING->PONG, TX[...], ipksent/TX done,
    RX[...]. Autonomous firmware (sst_rxtx2/sst_rawvary) prints: PAYLOAD: ... (RX),
    "TX: GALA###" (own TX), rxwin got=..., and (for TX beacons) the payload line
    like GALA%03d.
    """
    u = line.upper()
    now = time.time()
    if "READY." in u or u == "PONG":
        _sst["auto"] = False
        return
    if "PAYLOAD:" in u:
        _sst["auto"] = True
        _sst["mode"] = None
        _sst["ph_rx"] = True
        _sst["rx_msgs"].append(f"[{_ts()}] {line}")
        _sst["rx_msgs"] = _sst["rx_msgs"][-20:]
        _sst["activity"], _sst["activity_ts"] = "rx", now
    elif u.startswith("RX[") or "IPKVALID" in u:
        _sst["auto"] = True
        _sst["ph_rx"] = True
        _sst["activity"], _sst["activity_ts"] = "rx", now
    elif u.startswith("RXWIN GOT="):
        _sst["auto"] = True
        _sst["mode"] = None
        _sst["rxwin_seen"] = True   # alternator loop: this firmware TXes AND RXes
        _sst["ph_rx"] = True
        _sst["activity"], _sst["activity_ts"] = "rx", now
    elif re.fullmatch(r"GALA\d{3}", line):
        _sst["auto"] = True
        _sst["mode"] = None
        _sst["ph_tx"] = True
        _sst["rxwin_seen"] = False  # pure TX beacon, not the alternator
        _sst["activity"], _sst["activity_ts"] = "tx", now
    elif u.startswith("TX:"):
        # alternator/raw-vary announces the payload it is transmitting
        _sst["auto"] = True
        _sst["mode"] = None
        _sst["ph_tx"] = True
        _sst["tx_msgs"].append(f"[{_ts()}] {line}")
        _sst["tx_msgs"] = _sst["tx_msgs"][-20:]
        _sst["activity"], _sst["activity_ts"] = "tx", now
    elif u.startswith("TX[") or "IPKSENT" in u or "TX DONE" in u or "TIMEOUT" in u:
        # command-firmware TX echo / result report
        _sst["activity"], _sst["activity_ts"] = "tx", now


def _sst_drain(wait=0.12):
    """Consume pending serial output (no-op when idle) and update the state."""
    with _sst["lock"]:
        s = _sst["serial"]
        if s is None or not s.in_waiting:
            return
        try:
            buf = s.read(s.in_waiting)
            _sst["_linebuf"] = _sst.get("_linebuf", "") + buf.decode("ascii", "replace")
            while "\n" in _sst["_linebuf"]:
                line, _sst["_linebuf"] = _sst["_linebuf"].split("\n", 1)
                line = line.strip()
                if line:
                    _sst_ingest(line)
                    _sst["last_state"] = f"[{_ts()}] {line}"
        except Exception:
            pass


def _kill_tx():
    p = _tx["proc"]
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=3)
        except Exception:
            p.kill()
    _tx["proc"] = None
    # kill any orphan hackrf_transfer (e.g. from a previous crashed session)
    try:
        subprocess.run(["pkill", "-f", "hackrf_transfer"], timeout=3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _apply_preset(name: str):
    """Switch RF/DSP knobs to a link preset and sync the in-process decoder."""
    p = PRESETS[name]
    _cur["name"] = name
    # frequency stays independent — do NOT reset _link["freq"]
    dec.DATA_RATE = p["rate"]            # sync demodulator (module globals)
    dec.FREQ_DEVIATION = p["dev"]
    config.RX_LOWPASS_CUTOFF = p["cutoff"]


def start_tx(message: str):
    p = _preset()
    freq = _link_freq()
    with _lock:
        _kill_tx()
        try:
            subprocess.run(
                [sys.executable, GEN_PY, "--text", message, "--out", IQ_FILE,
                 "--sample-rate", str(p["tx_rate"]), "--data-rate", str(p["rate"]),
                 "--deviation", str(p["dev"]), "--freq", str(freq),
                 "--digital-gain", str(p["digital_gain"])],
                check=True, stdout=subprocess.DEVNULL)
            cmd = ["hackrf_transfer"]
            dev = _sel.get("hackrf_serial") or _sel.get("hackrf_rx_serial")
            if dev:
                cmd += ["-d", dev]
            cmd += ["-t", IQ_FILE, "-f", str(freq), "-s", str(p["tx_rate"]),
                    "-a", "1" if _tx_cfg["amp"] else "0",
                    "-x", str(min(int(_tx_cfg["tx_gain"]), config.TX_GAIN_MAX)),
                    "-p", "1", "-R"]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            _tx["proc"] = proc
            _tx["msg"] = message
        except FileNotFoundError as e:
            _tx["proc"] = None
            _tx["msg"] = message
            print(f"[start_tx] SDR binary missing: {e}")


def stop_tx():
    with _lock:
        _kill_tx()


def _rx_cmd(outfile: str, nsamples: int):
    """Build the receiver command (RTL-SDR rtl_sdr, or a HackRF as RX)."""
    p = _preset()
    tune = _rx_tune()                                  # tunes 100 kHz below the desired RX freq
    if _sel["rx_kind"] == "hackrf":
        return ["hackrf_transfer", "-r", outfile,
                "-d", _sel["hackrf_rx_serial"], "-f", str(tune),
                "-s", str(_rx_rate()), "-l", str(int(_sel["rx_lna"])),
                "-g", str(int(_sel["rx_vga"])), "-n", str(nsamples)]
    cmd = ["rtl_sdr"]
    if int(_sel["rx_index"]) >= 0:
        cmd += ["-d", str(int(_sel["rx_index"]))]
    cmd += ["-f", str(tune), "-s", str(_rx_rate()), "-g", str(config.RX_GAIN),
            "-n", str(nsamples), outfile]
    return cmd


def _list_rtl():
    """Enumerate RTL-SDR device indices (0..N-1)."""
    global _rtl_cache
    if _cap_lock.locked():                 # don't open the device mid-capture
        return _rtl_cache
    try:
        r = subprocess.run(["rtl_test", "-t"], stdout=subprocess.PIPE,
                           text=True, timeout=15, stderr=subprocess.STDOUT)
        import re
        m = re.search(r"Found (\d+) device", r.stdout)
        _rtl_cache = list(range(int(m.group(1)))) if m else []
    except Exception:
        pass
    return _rtl_cache


def _hackrf_match(sel_serial, listed_serials):
    """Match HackRF serial ignoring leading zeros."""
    if not sel_serial:
        return False
    sel = sel_serial.lstrip("0")
    return any(s.lstrip("0") == sel for s in listed_serials)

def _list_hackrf():
    """Enumerate HackRF serial numbers (hex only; drop wedged/garbage reads)."""
    import re as _re
    try:
        r = subprocess.run(["hackrf_info"], stdout=subprocess.PIPE,
                           text=True, timeout=15, stderr=subprocess.STDOUT)
        serials = []
        for line in r.stdout.splitlines():
            if "Serial number:" in line:
                s = line.split("Serial number:")[-1].strip()
                if _re.fullmatch(r"[0-9A-Fa-f]{8,}", s):
                    serials.append(s)
        return serials
    except Exception:
        return []


def _signal_metrics(iq, fs):
    """Quick RF-quality metrics for gain/antenna tuning."""
    import numpy as np
    rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
    peak = float(np.max(np.abs(iq)))
    db = lambda x: 20 * np.log10(x) if x > 0 else -60
    # top spectral peaks in +-250 kHz (excluding DC)
    n = min(len(iq), 1 << 16)
    seg = iq[:n] - iq[:n].mean()
    w = np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs))
    m = (np.abs(f) < 250_000) & (np.abs(f) > 1500)
    idx = np.argsort(np.where(m, w, 0.0))[-4:][::-1]
    fc = dec.carrier_offset(iq, fs, lo_center=_preset()["freq"] and 100_000)
    # signal quality
    rms_db = db(rms)
    if rms_db < -30:
        quality = "No signal"
    elif rms_db < -20:
        quality = "Weak"
    elif rms_db < -10:
        quality = "Fair"
    else:
        quality = "Strong"
    # strongest peak (exclude DC)
    top_f = float(f[idx[0]]) if len(idx) else 0
    # genuine clipping: int8 full-scale (127/128=0.992) is NORMAL for a strong
    # bursty signal near fs — only scream OVERLOAD when the whole capture is
    # running hot (high RMS too), not for brief burst peaks.
    saturating = bool(peak > 0.98 and rms_db > -12)
    return {
        "rms_dbfs": round(rms_db, 1),
        "peak_dbfs": round(db(peak), 1),
        "carrier": round(float(fc)),
        "carrier_khz": round(float(fc) / 1000, 1),
        "saturating": saturating,
        "quality": quality,
        "peak_freq_khz": round(top_f / 1000, 1),
    }


def _run_rtl(cmd: list, timeout: float, attempts: int = 4):
    """Run an RX capture with retries. The RTL-SDR on a USB passthrough can
    hiccup / briefly collide with device enumeration, so retry a few times.
    Tracks the Popen so a new Capture can preempt it."""
    import time
    last = None
    for _ in range(attempts):
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        _cap_proc["proc"] = p
        try:
            ret = p.wait(timeout=timeout)
            if ret == 0:
                return
            # -15/-9 = terminated by us (preempt) → don't retry, abort
            if ret in (-15, -9) or ret < 0:
                raise subprocess.CalledProcessError(ret, cmd)
            raise subprocess.CalledProcessError(ret, cmd)
        except subprocess.TimeoutExpired as e:
            try:
                p.kill()
            except Exception:
                pass
            last = e
            time.sleep(0.5)
        except subprocess.CalledProcessError as e:
            last = e
            if e.returncode in (-15, -9) or (e.returncode or 0) < 0:
                raise
            time.sleep(0.5)
        finally:
            _cap_proc["proc"] = None
    raise last


def _capture(path, seconds):
    """Capture `seconds` of IQ to `path` using the selected RX device."""
    p = _preset()
    nsamples = int(_rx_rate() * seconds)
    _run_rtl(_rx_cmd(path, nsamples), seconds + 10)
    return path


def decode_capture(path: str) -> list:
    # Prefer strict CRC-valid packets. Try the exact payload length the app
    # is currently transmitting first (fast, correct for the self-loop), then
    # the legacy 7-byte SST01 format. NEVER scan all 32 lengths (AGENTS §21):
    # that turns a ~3s decode into minutes. On a noisy bench the wideband CRC
    # flips ~1 bit/packet, so fall back to majority-vote of the repeated
    # beacon payloads, capped to a single length.
    lens = []
    if _tx.get("msg"):
        lens.append(len(_tx["msg"].encode("utf-8", errors="replace")))
    lens.append(dec.DEFAULT_PAYLOAD_LEN)
    packets = []
    best_effort = False
    for pl in dict.fromkeys(lens):
        if 1 <= pl <= dec.MAX_PAYLOAD_LEN:
            try:
                packets = dec.decode(path, _rx_rate(), payload_len=pl)
            except Exception:
                packets = []
            if packets:
                break
    if not packets:
        try:
            packets = dec.decode_majority(
                path, _rx_rate(), payload_len=dec.DEFAULT_PAYLOAD_LEN)
        except Exception:
            packets = []
        best_effort = bool(packets)
    result = []
    for pkt in packets:
        payload, crc_ok, crc_calc, crc_rx, phase, = pkt[0], pkt[1], pkt[2], pkt[3], pkt[4]
        n = pkt[5] if len(pkt) > 5 else 1
        result.append({
            "text": payload.decode("ascii", errors="replace"),
            "len": len(payload),
            "crc_ok": bool(crc_ok),
            "best_effort": bool(best_effort),
            "occurrences": n,
            "crc": f"0x{crc_rx:04X}",
            "hex": payload.hex(),
        })
    return result


def _decode_json():
    if not os.path.exists(RX_FILE):
        return jsonify({"ok": False, "error": "No capture yet — run capture first"}), 400
    try:
        packets = decode_capture(RX_FILE)
        p = _preset()
        iq = dec.read_iq(RX_FILE)
        m = _signal_metrics(iq, _rx_rate())
        m["decoded"] = len(packets)
        if packets:
            # a sparse bursty beacon looks quiet in whole-capture RMS — if the
            # decoder got CRC-valid packets, the signal IS present.
            m["quality"] = "Strong"
        return jsonify({"ok": True, "count": len(packets), "packets": packets,
                        "metrics": m})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/")
def index():
    r = render_template_string(HTML)
    from flask import Response
    resp = Response(r)
    resp.headers["Cache-Control"]="no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]="no-cache"
    return resp


def _tx_running():
    return _tx["proc"] is not None and _tx["proc"].poll() is None


@app.get("/status")
def status():
    with _lock:
        running = _tx_running()
        return jsonify({"tx_running": running, "message": _tx["msg"],
                        "preset": _cur["name"],
                        "rate": _preset()["rate"], "freq": _link_freq(), "rx_freq": _rx_freq(),
                        "rx_tune": _rx_tune(),
                        "rx_kind": _sel["rx_kind"], "device": _sel.get("rx_index"),
                        "rx_lna": _sel["rx_lna"], "rx_vga": _sel["rx_vga"],
                        "rx_hackrf": _sel.get("hackrf_rx_serial"),
                        "tx_gain": _tx_cfg["tx_gain"], "amp": _tx_cfg["amp"]})


@app.get("/presets")
def presets():
    return jsonify({"presets": [{"name": n, "label": PRESETS[n]["label"],
                                 "rate": PRESETS[n]["rate"]}
                                for n in PRESETS],
                    "current": _cur["name"]})


@app.post("/preset")
def preset():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if name not in PRESETS:
        return jsonify({"ok": False, "error": "unknown preset"}), 400
    _apply_preset(name)
    if _tx_running():
        start_tx(_tx["msg"] or config.DEFAULT_MESSAGE)   # live-restart if on air
    return jsonify({"ok": True, "current": _cur["name"], "preset": _preset()})


@app.post("/freq")
def freq():
    body = request.get_json(silent=True) or {}
    freq_hz = _parse_freq(body, None)
    rx_hz = _parse_freq(body, "rx_")
    if freq_hz is None and rx_hz is None:
        return jsonify({"ok": False, "error": "no frequency"}), 400
    if freq_hz is not None:
        _link["freq"] = freq_hz
    if rx_hz is not None:
        _link["rx_freq"] = rx_hz
    if freq_hz is not None and _tx_running():
        start_tx(_tx["msg"] or config.DEFAULT_MESSAGE)   # retune live
    return jsonify({"ok": True, "freq_hz": _link["freq"],
                    "rx_freq_hz": _link["rx_freq"], "rx_tune_hz": _rx_tune()})


def _parse_freq(body, prefix: str):
    """Parse a frequency from the body keys '<prefix>freq_mhz' / '<prefix>freq_hz'."""
    prefix = prefix or ""
    mhz = body.get(prefix + "freq_mhz")
    hz = body.get(prefix + "freq_hz")
    if mhz is not None:
        try:
            hz = int(float(mhz) * 1e6)
        except (TypeError, ValueError):
            return None
    if hz is None:
        return None
    hz = int(hz)
    if not (100_000_000 <= hz <= 6_000_000_000):
        return None
    return hz


@app.get("/devices")
def devices():
    return jsonify({"rtl": _list_rtl(), "hackrf": _list_hackrf(),
                    "selected": _sel})


@app.post("/select")
def select():
    body = request.get_json(silent=True) or {}
    if "rx_kind" in body and body["rx_kind"] in ("rtl", "hackrf"):
        _sel["rx_kind"] = body["rx_kind"]
    if "rx_index" in body and str(body["rx_index"]).lstrip("-").isdigit():
        _sel["rx_index"] = int(body["rx_index"])
    if "hackrf_rx_serial" in body:
        _sel["hackrf_rx_serial"] = str(body["hackrf_rx_serial"]) or None
    if "hackrf_serial" in body:
        _sel["hackrf_serial"] = str(body["hackrf_serial"]) or None
    if "rx_lna" in body:
        _sel["rx_lna"] = max(0, min(40, int(body["rx_lna"])))
    if "rx_vga" in body:
        _sel["rx_vga"] = max(0, min(62, int(body["rx_vga"])))
    return jsonify({"ok": True, "selected": _sel})


@app.post("/txcfg")
def txcfg():
    body = request.get_json(silent=True) or {}
    if "tx_gain" in body:
        _tx_cfg["tx_gain"] = max(0, min(config.TX_GAIN_MAX, int(body["tx_gain"])))
    if "amp" in body:
        _tx_cfg["amp"] = bool(body["amp"])
    # restart TX only if it's already on air; else the knob is applied on next Send
    if _tx_running():
        start_tx(_tx["msg"] or config.DEFAULT_MESSAGE)
    return jsonify({"ok": True, "tx_cfg": _tx_cfg})


@app.post("/send")
def send():
    msg = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not msg:
        return jsonify({"ok": False, "error": "Message is empty"}), 400
    if len(msg.encode("utf-8")) > config.MAX_PACKET_LEN:
        return jsonify({"ok": False, "error": f"Message too long (max {config.MAX_PACKET_LEN} bytes)"}), 400
    start_tx(msg)
    return jsonify({"ok": True, "message": msg})


@app.post("/sendfile")
def sendfile():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file selected"}), 400
    try:
        txt = f.read().decode("utf-8", errors="replace").strip()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Read error: {e}"}), 400
    if not txt:
        return jsonify({"ok": False, "error": "File is empty"}), 400
    b = txt.encode("utf-8")
    if len(b) > config.MAX_PACKET_LEN:
        # satellite: auto-truncate to first packet; UI will warn
        return jsonify({"ok": False, "error": f"File too large ({len(b)} B > {config.MAX_PACKET_LEN} B max per packet). Split into ≤64 B chunks."}), 400
    start_tx(txt)
    return jsonify({"ok": True, "message": txt, "filename": f.filename})


@app.post("/stop")
def stop():
    stop_tx()
    return jsonify({"ok": True})


@app.post("/capture")
def capture():
    if not _cap_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "A capture is already running"}), 409
    try:
        # validate the selected RX device is actually present
        if _sel["rx_kind"] == "hackrf":
            if not _sel.get("hackrf_rx_serial") or not _hackrf_match(_sel["hackrf_rx_serial"], _list_hackrf()):
                return jsonify({"ok": False, "error": "HackRF not connected — select a connected receiver"}), 400
        else:
            rtl = _list_rtl()
            if not rtl or int(_sel["rx_index"]) not in rtl:
                return jsonify({"ok": False, "error": "RTL-SDR not connected — select a connected receiver"}), 400
        if os.path.exists(RX_FILE):
            os.remove(RX_FILE)
        _run_rtl(_rx_cmd(RX_FILE, _rx_samples()), 30)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        _cap_lock.release()


@app.post("/decode")
def decode_route():
    return _decode_json()


@app.post("/signal")
def signal():
    """Quick (~1.2 s) RF-level check for LNA/VGA / antenna tuning."""
    if not _cap_lock.acquire(blocking=False):
        pp = _cap_proc.get("proc")
        if pp and pp.poll() is None:
            try: pp.terminate(); pp.wait(timeout=2)
            except Exception:
                try: pp.kill()
                except Exception: pass
        if not _cap_lock.acquire(timeout=10):
            return jsonify({"ok": False, "error": "A capture is already running"}), 409
    try:
        # validate the selected RX device is actually present
        if _sel["rx_kind"] == "hackrf":
            if not _sel.get("hackrf_rx_serial") or not _hackrf_match(_sel["hackrf_rx_serial"], _list_hackrf()):
                return jsonify({"ok": False, "error": "HackRF not connected — select a connected receiver"}), 400
        else:
            rtl = _list_rtl()
            if not rtl or int(_sel["rx_index"]) not in rtl:
                return jsonify({"ok": False, "error": "RTL-SDR not connected — select a connected receiver"}), 400
        if os.path.exists(RX_FILE):
            os.remove(RX_FILE)
        try:
            _capture(RX_FILE, 1.2)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        _cap_lock.release()
    p = _preset()
    iq = dec.read_iq(RX_FILE)
    return jsonify({"ok": True, "rate": p["rate"], "rx_kind": _sel["rx_kind"],
                    "metrics": _signal_metrics(iq, _rx_rate())})


@app.post("/receive")
def receive():
    """One-shot convenience: capture then decode. Preempts any in-progress capture."""
    if not _cap_lock.acquire(blocking=False):
        pp = _cap_proc.get("proc")
        if pp and pp.poll() is None:
            try: pp.terminate(); pp.wait(timeout=2)
            except Exception:
                try: pp.kill()
                except Exception: pass
        if not _cap_lock.acquire(timeout=10):
            return jsonify({"ok": False, "error": "A capture is already running"}), 409
    try:
        # validate the selected RX device is actually present
        if _sel["rx_kind"] == "hackrf":
            if not _sel.get("hackrf_rx_serial") or not _hackrf_match(_sel["hackrf_rx_serial"], _list_hackrf()):
                return jsonify({"ok": False, "error": "HackRF not connected — select a connected receiver"}), 400
        else:
            rtl = _list_rtl()
            if not rtl or int(_sel["rx_index"]) not in rtl:
                return jsonify({"ok": False, "error": "RTL-SDR not connected — select a connected receiver"}), 400
        if os.path.exists(RX_FILE):
            os.remove(RX_FILE)
        _run_rtl(_rx_cmd(RX_FILE, _rx_samples()), 30)
        return _decode_json()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        _cap_lock.release()


@app.get("/spectrum")
def spectrum():
    """0.4 s FFT snapshot for waterfall. Preempts if needed, returns dBFS array."""
    if not _cap_lock.acquire(blocking=False):
        # preempt long capture
        pp = _cap_proc.get("proc")
        if pp and pp.poll() is None:
            try: pp.terminate(); pp.wait(timeout=1)
            except Exception:
                try: pp.kill()
                except Exception: pass
        if not _cap_lock.acquire(timeout=3):
            return jsonify({"ok": False, "error": "busy"}), 409
    tmp = os.path.join(DATA, "_spec.iq")
    try:
        if _sel["rx_kind"] == "hackrf":
            if not _sel.get("hackrf_rx_serial") or not _hackrf_match(_sel["hackrf_rx_serial"], _list_hackrf()):
                return jsonify({"ok": False, "error": "HackRF not connected"}), 400
        else:
            if not _list_rtl() or int(_sel["rx_index"]) not in _list_rtl():
                return jsonify({"ok": False, "error": "RTL-SDR not connected"}), 400
        ns = int(_rx_rate() * 0.4)
        _run_rtl(_rx_cmd(tmp, ns), timeout=5)
        import numpy as np
        iq = dec.read_iq(tmp)
        n = min(len(iq), 2048)
        seg = iq[:n] - np.mean(iq[:n])
        w = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(n)))) ** 2
        f = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / _rx_rate()))
        # downsample to 256 bins for UI
        idx = np.linspace(0, len(w)-1, 256).astype(int)
        pwr = 10*np.log10(np.maximum(w[idx], 1e-12))
        # absolute dB (not normalized) — waterfall shows real power difference
        # HackRF full-scale approx 0 dB, noise floor ~ -60
        pwr = pwr.tolist()
        freqs = (f[idx]/1000).tolist()  # kHz offset from tune
        return jsonify({"ok": True, "freqs_khz": freqs, "pwr_db": pwr,
                        "tune_hz": _rx_tune(), "carrier_hz": _link_freq(),
                        "rx_freq_hz": _rx_freq()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        _cap_lock.release()


# ---- SST01 mode control ----
def _sst_enter(mode):
    """Enter a mode: 'rx', 'tx', 'idle'. Returns status text."""
    if _sst["serial"] is not None or _sst_open():
        if mode == "tx":
            return _sst_cmd("IDLE") or _sst_cmd("TX:HELLO") or "ok"
        _sst_cmd("IDLE")            # return to register-access state first
        resp = _sst_cmd(mode.upper(), wait=0.5)
        _sst["mode"] = mode
        return resp or "ok"
    return _sst["error"] or "not connected"


@app.get("/sst01/status")
def sst01_status():
    present = _sst_open() if _sst["serial"] is None else _sst["present"]
    _sst_drain()
    activity = _sst["activity"] if (time.time() - _sst["activity_ts"]) <= _ACTIVITY_AGE else None
    cmd_mode = None if _sst["auto"] else _sst["mode"]   # commands don't apply to autonomous firmware
    if not present:
        state = "offline"
    elif _sst["auto"] is True:
        if _sst["rxwin_seen"]:
            state = "AUTO RX/TX"                       # alternator: both TX and RX
        elif _sst["ph_tx"] and not _sst["ph_rx"]:
            state = "TX"                               # autonomous TX beacon
        elif _sst["ph_rx"]:
            state = "RX"                               # autonomous RX-only
        else:
            state = "AUTO"
    else:
        state = cmd_mode or ("connected" if present else "offline")
    return jsonify({
        "ok": True,
        "present": present,
        "state": state,
        "mode": cmd_mode,
        "auto": _sst["auto"],
        "activity": activity,
        "last_state": _sst["last_state"],
        "rx_msgs": _sst["rx_msgs"][-20:],
        "tx_msgs": _sst["tx_msgs"][-20:],
        "error": _sst["error"],
    })


@app.post("/sst01/mode")
def sst01_mode():
    data = request.get_json(force=True) or {}
    mode = (data.get("mode") or "").lower()
    if mode not in ("rx", "tx", "idle"):
        return jsonify({"ok": False, "error": "mode must be rx/tx/idle"}), 400
    if not _sst_open():
        return jsonify({"ok": False, "error": _sst["error"]}), 400
    _sst_drain(0.05)
    if _sst["auto"]:
        return jsonify({"ok": False,
                        "error": "radio is running its autonomous TX/RX firmware — "
                                 "mode commands need the command firmware "
                                 "(nucleo_firmware/sst_rx_tx/sst_rx_tx.ino)"}), 400
    _sst_cmd("IDLE")
    if mode == "tx":
        text = (data.get("text") or "").strip() or "HELLO FROM GALAMAD"
        resp = _sst_cmd("TX:" + text, wait=0.5)
        _sst["mode"] = "tx"
        return jsonify({"ok": True, "mode": "tx", "text": text, "resp": resp})
    resp = _sst_cmd(mode.upper(), wait=0.5)
    _sst["mode"] = mode
    return jsonify({"ok": True, "mode": mode, "resp": resp})


@app.post("/sst01/send")
def sst01_send():
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty text"}), 400
    if not _sst_open():
        return jsonify({"ok": False, "error": _sst["error"]}), 400
    _sst_drain(0.05)
    if _sst["auto"]:
        return jsonify({"ok": False,
                        "error": "radio is running its autonomous TX/RX firmware — "
                                 "TX commands need the command firmware "
                                 "(nucleo_firmware/sst_rx_tx/sst_rx_tx.ino)"}), 400
    _sst_cmd("IDLE")
    resp = _sst_cmd("TX:" + text, wait=0.6)
    _sst["mode"] = "tx"
    return jsonify({"ok": True, "text": text, "resp": resp})


HTML = r"""
<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GALAMAD GFSK Console</title>
<style>
  :root{
    --bg0:#120806; --bg1:#2a1007; --card:#221008; --card2:#2c150b;
    --gold:#f2b13a; --gold2:#e08a1e; --cocoa:#1a0d06; --ink:#150904;
    --red:#c73a2b; --green:#3fa15c; --txt:#f8ecd4; --mut:#d0a76f;
    --ok:#4cd37f; --err:#ff7a6b; --acc:#f2b13a;
    --kente:repeating-linear-gradient(90deg,#e8a33d 0 34px,#1c0d06 34px 46px,
              #2f8f57 46px 74px,#c73a2b 74px 100px,#f2b13a 100px 130px,#2b3a67 130px 152px,
              #e8a33d 152px 186px);
  }
  *{box-sizing:border-box}
  body{margin:0;color:var(--txt);
    font-family:'Trebuchet MS','Segoe UI',system-ui,sans-serif;min-height:100vh;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(242,177,58,.18), transparent 60%),
      radial-gradient(900px 500px at 12% 104%, rgba(199,58,43,.18), transparent 60%),
      radial-gradient(900px 500px at 90% 100%, rgba(63,161,92,.14), transparent 60%),
      linear-gradient(180deg,var(--bg0),var(--bg1));
    display:flex;flex-direction:column;align-items:center;}
  body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.06;
    background:repeating-linear-gradient(45deg,var(--gold) 0 8px,transparent 8px 22px),
               repeating-linear-gradient(-45deg,var(--gold2) 0 8px,transparent 8px 22px);}
  .wrap{width:min(960px,96vw);padding:26px 0 40px;position:relative;z-index:1}
  .hero{text-align:center;margin-bottom:6px}
  .kente-banner{height:12px;border-radius:20px;background:var(--kente);
     box-shadow:0 0 0 2px var(--ink),0 0 22px rgba(242,177,58,.35);}
  .brand{font-size:15px;letter-spacing:6px;color:var(--gold2);font-weight:700;margin:14px 0 0;text-transform:uppercase}
  h1{font-size:clamp(26px,5vw,40px);margin:2px 0 6px;color:var(--txt);font-weight:800;letter-spacing:2px;
     text-shadow:0 2px 0 var(--cocoa),0 0 26px rgba(242,177,58,.35)}
  h1 span{color:var(--gold)}
  .sub{color:var(--mut);font-size:12px;max-width:560px;margin:0 auto 22px;line-height:1.6}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:760px){.grid{grid-template-columns:1fr}}
  .card{background:linear-gradient(180deg,var(--card2),var(--card));
     border:2px solid var(--gold2);border-radius:18px;overflow:hidden;
     box-shadow:0 12px 30px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.05)}
  .card .top{height:9px;background:var(--kente)}
  .card .inner{padding:18px 18px 20px}
  .card h2{font-size:15px;margin:0 0 3px;color:var(--gold);letter-spacing:1px;font-weight:800}
  .card .ct{color:var(--mut);font-size:11px;margin-bottom:16px;line-height:1.5}
  label{font-size:12px;color:var(--gold2);display:block;margin-bottom:6px;font-weight:700}
  input[type=text]{width:100%;padding:12px;border-radius:10px;border:2px solid var(--gold2);
     background:var(--ink);color:var(--txt);font-family:inherit;font-size:15px;
     box-shadow:inset 0 2px 8px rgba(0,0,0,.5)}
  input:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 3px rgba(242,177,58,.3)}
  select{width:100%;padding:10px;border-radius:10px;border:2px solid var(--gold2);
     background:var(--ink);color:var(--txt);font-family:inherit;font-size:13px;
     box-shadow:inset 0 2px 8px rgba(0,0,0,.5);cursor:pointer}
  select:focus{outline:none;border-color:var(--gold)}
  .dev{display:flex;gap:8px;align-items:center;background:var(--ink);
     border:1px solid var(--gold2);border-radius:10px;padding:7px 10px;margin-top:12px}
  .dev label{margin:0;font-size:11px;white-space:nowrap}
  .dev .lbl{color:var(--gold)}.dev .val{color:var(--mut)}
  .dev input[type=number]{width:66px;padding:6px 8px;border-radius:8px;border:2px solid var(--gold2);
     background:var(--ink);color:var(--txt);font-size:13px;font-family:inherit}
  .btn-gray{background:linear-gradient(180deg,#4a6076,#33506a)}
  .sig{margin-top:12px;border:1px solid var(--gold2);border-radius:12px;padding:10px 12px;background:var(--ink)}
  .sig .label{font-size:11px;color:var(--gold2);font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
  .meter{height:14px;border-radius:8px;background:#0c0703;border:1px solid var(--gold2);overflow:hidden;margin-top:4px}
  .meter .bar{height:100%;width:0%;background:linear-gradient(90deg,var(--green),var(--gold),var(--red));transition:width .4s ease}
  .lstats{display:flex;gap:12px;margin-top:8px;font-size:12px;color:var(--txt);flex-wrap:wrap;align-items:center}
  .lstats b{color:var(--gold)}
  .quality{font-weight:800;font-size:12px;padding:2px 8px;border-radius:6px;display:inline-block}
  .quality.strong{background:#1a3d1a;color:var(--ok)}
  .quality.fair{background:#3d3a1a;color:var(--gold)}
  .quality.weak{background:#3d1a1a;color:var(--err)}
  .quality.none{background:#1a1a1a;color:var(--mut)}
  .pk{font-size:10px;color:var(--mut);margin-top:6px;display:flex;gap:10px;flex-wrap:wrap}
  .satflag{color:var(--err);font-weight:800;font-size:11px}
  .cfg{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-top:12px}
  .cfg label{font-size:11px;color:var(--gold2);font-weight:700}
  .cfg input[type=number]{width:80px;padding:8px 10px;border-radius:9px;border:2px solid var(--gold2);
     background:var(--ink);color:var(--txt);font-size:14px;font-family:inherit;
     -moz-appearance:textfield;appearance:textfield}
  /* kill native spin arrows (they overlap the digits) */
  .cfg input[type=number]::-webkit-outer-spin-button,
  .cfg input[type=number]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
  .freqrow{flex:1;min-width:200px}
  .freqrow label{font-size:12px;color:var(--gold2);font-weight:700}
  .freqbox{display:flex;align-items:center;gap:8px;margin-top:6px;width:min(230px,96%);
     background:var(--ink);border:2px solid var(--gold2);border-radius:10px;padding:7px 12px}
  .freqbox input{flex:1;background:transparent;border:0;color:var(--txt);font-size:16px;
     font-family:inherit;text-align:right;min-width:0}
  .freqbox input:focus{outline:none}
  .freqbox span{color:var(--mut);font-size:12px}
  .txpwr input[type=range]{width:150px;accent-color:var(--gold);cursor:pointer}
  .txpwr{flex:1;min-width:180px}
  .txpwr b{color:var(--gold)}
  .txpwr .lim{font-size:10px;color:var(--mut);margin-top:3px}
  .cfg .chk{display:flex;gap:6px;align-items:center;color:var(--txt);font-size:12px}
  .cfg .chk input{width:16px;height:16px;accent-color:var(--gold)}
  .row{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
  button{border:0;border-radius:12px;padding:11px 18px;cursor:pointer;font-family:inherit;
     font-size:13px;font-weight:800;letter-spacing:.4px;color:#fff;
     transition:transform .08s ease,filter .15s ease}
  button:hover{filter:brightness(1.1)}button:active{transform:translateY(1px)}
  button:disabled{opacity:.45;cursor:not-allowed;filter:grayscale(.4)}
  /* transmitting animation: radiating arcs */
  .txlive{display:none;align-items:center;gap:6px;margin-left:6px;color:var(--ok);
     font-weight:800;font-size:11px;letter-spacing:1px}
  .txlive.show{display:inline-flex}
  .txlive .rx{width:10px;height:10px;border:2px solid var(--ok);border-radius:50%;
     animation:wave 1.2s infinite ease-out}
  .txlive .rx.d2{animation-delay:.3s}.txlive .rx.d3{animation-delay:.6s}
  @keyframes wave{0%{transform:scale(.5);opacity:1}100%{transform:scale(2.1);opacity:0}}
  .dot.on{background:var(--ok);color:var(--ok)}
  @keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
  .btn-gold{background:linear-gradient(180deg,var(--gold),var(--gold2));color:var(--ink)}
  .btn-red{background:linear-gradient(180deg,#d94f3b,var(--red))}
  .btn-green{background:linear-gradient(180deg,#4cc47a,var(--green));color:var(--ink)}
  .pill{display:inline-flex;align-items:center;gap:8px;font-size:12px;color:var(--mut);
     background:var(--ink);padding:6px 12px;border-radius:20px;border:1px solid var(--gold2)}
  .pill.act{background:linear-gradient(180deg,var(--gold),var(--gold2));
     color:var(--ink);border-color:transparent;font-weight:800}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block;box-shadow:0 0 6px currentColor}
  .dot.on{background:var(--ok);color:var(--ok)}.dot.off{background:#645446;color:#645446}
  ul{list-style:none;margin:12px 0 0;padding:0;max-height:320px;overflow:auto}
  li{padding:10px 12px;border-bottom:1px solid rgba(224,138,30,.2);font-size:14px;
     display:flex;justify-content:space-between;gap:12px;align-items:center}
  li:nth-child(odd){background:rgba(255,255,255,.03)}li:last-child{border-bottom:0}
  ul::-webkit-scrollbar{width:9px}ul::-webkit-scrollbar-thumb{background:var(--gold2);border-radius:8px}
  .badge{font-size:10px;padding:3px 9px;border-radius:20px;font-weight:800;white-space:nowrap}
  .badge.ok{background:var(--ok);color:var(--ink)}.badge.bad{background:var(--err);color:var(--ink)}
  #busy{font-size:13px;margin-top:12px;min-height:18px;font-weight:700}
  .msg{font-size:12px;margin-top:10px;min-height:16px}
  .ok{color:var(--ok)}.err{color:var(--err)}
  .footer{text-align:center;color:var(--mut);font-size:11px;margin-top:26px;letter-spacing:2px;text-transform:uppercase}
  .footer b{color:var(--gold)}
  .flake{color:var(--gold);font-size:22px;margin-top:8px;letter-spacing:14px}
</style></head><body>
<div class="wrap">
  <div class="hero">
    <div class="kente-banner"></div>
    <div class="brand">Galamad Aerospace</div>
    <h1>GALAMAD <span>GFSK</span></h1>
    <div class="flake">&#9670; &#9670; &#9670;</div>
    <div class="sub">HackRF One &#8594; RTL-SDR &middot; GFSK &middot; BT 0.5 &middot; no GNU Radio</div>
  </div>
  <div class="dev" style="max-width:420px;margin:0 auto 6px"><span class="lbl">Link&nbsp;rate</span><select id="preset"></select></div>
  <div class="grid">
    <div class="card">
      <div class="top"></div>
      <div class="inner">
        <h2>TRANSMIT</h2>
        <div class="ct">Regenerates the GFSK IQ and loops it out of the HackRF</div>
        <div class="dev"><span class="lbl">Sending&nbsp;device</span><select id="hfsel"></select></div>
        <label style="margin-top:12px">Message to send (max <span data-m></span> bytes)</label>
        <input id="msg" type="text" value="HELLO FROM GALAMAD AEROSPACE" spellcheck="false">
        <div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap">
          <input id="file" type="file" accept=".txt,.csv,.json,.log" style="flex:1;min-width:180px;color:var(--mut);font-size:12px">
          <button class="btn-gray" style="padding:8px 14px;font-size:12px" onclick="sendFile()">Send File</button>
          <span id="fname" style="font-size:11px;color:var(--mut)"></span>
        </div>
        <div style="font-size:10px;color:var(--mut)">Text file ≤64 B → single packet. Larger files: split first.</div>
        <div class="cfg">
          <div class="txpwr">
            <label>TX power &nbsp;<b id="txgainval"></b> dB</label>
            <input id="txgain" type="range" min="0" max="40" step="1" value="30">
            <div class="lim">Safety limit &le; 40 dB</div>
          </div>
          <div class="chk"><input id="amp" type="checkbox"><label for="amp">Amp ON</label></div>
        </div>
        <div class="cfg">
          <div class="freqrow">
            <label>TX frequency</label>
            <div class="freqbox"><input id="freq" type="number" min="100" max="6000" step="0.001" placeholder="437.000"><span>MHz</span></div>
            <div style="font-size:10px;color:var(--mut);margin-top:2px">sending on <b id="txfreq">--</b> MHz</div>
          </div>
        </div>
        <div class="row">
          <button id="sendbtn" class="btn-gold" onclick="send()">Send / Update</button>
          <button id="stopbtn" class="btn-red" onclick="stopTx()" disabled>Stop</button>
        </div>
        <div class="row">
          <span class="pill"><span id="txdot" class="dot"></span>TX: <span id="txstate">idle</span></span>
          <span id="txlive" class="txlive"><span class="rx"></span><span class="rx d2"></span><span class="rx d3"></span>TRANSMITTING</span>
        </div>
        <div id="smsgo" class="msg">Idle — press Send / Update to transmit</div>
      </div>
    </div>
    <div class="card">
      <div class="top"></div>
      <div class="inner">
        <h2>RECEIVE</h2>
        <div class="ct">Captures ~<span data-s></span> s from <span id="rxkind">the receiver</span>, then demodulates + decodes</div>
        <div class="dev"><span class="lbl">Receiving&nbsp;device</span><select id="rxsel"></select></div>
        <div class="dev rxgain" id="rxgainrow" style="display:none">
          <span class="lbl">LNA</span><input id="rxlna" type="number" min="0" max="40" step="8">
          <span class="lbl">VGA</span><input id="rxvga" type="number" min="0" max="62" step="2">
        </div>
        <div class="freqrow">
          <label>RX frequency</label>
          <div class="freqbox"><input id="rxbox" type="number" min="100" max="6000" step="0.001" placeholder="437.000"><span>MHz</span></div>
          <div style="font-size:10px;color:var(--mut);margin-top:2px">listening to <b id="rxfreq">--</b> MHz &middot; RX tune <b id="rxtune">--</b> MHz (carrier lands at +100 kHz)</div>
        </div>
        <div class="sig">
          <div class="label">Signal Strength</div>
          <div class="row"><button class="btn-green" onclick="recv()">Capture &amp; Decode</button>
            <button class="btn-gray" onclick="checkSignal()">Check signal</button></div>
          <div class="meter"><div id="lvlbar" class="bar"></div></div>
          <div class="lstats">
            <span id="quality" class="quality none">--</span>
            <span id="lvl">RMS: --</span>
            <span id="pk">Peak: --</span>
            <span id="satflag" style="display:none">OVERLOAD</span>
          </div>
          <div id="carrierinfo" style="font-size:11px;color:var(--mut);margin-top:4px"></div>
          <div id="peaks" class="pk"></div>
        </div>
        <div id="busy"></div>
        <div class="msg">Decoded messages:</div>
        <ul id="rxlist"><li style="color:var(--mut)">Nothing yet</li></ul>
      </div>
    </div>
    <div class="card">
      <div class="top"></div>
      <div class="inner">
        <h2>SST01 TRANSCEIVER <span class="pill" id="sstpill" style="margin-left:8px"><span id="sstdot" class="dot off"></span><span id="sststate">connecting</span></span></h2>
        <div class="ct">Directs the Nucleo-driven SST01 UHF radio: RX (receive + print packet over serial) or TX (send text).</div>
        <div class="dev"><span class="lbl">Radio</span><span class="val" id="sstport">/dev/ttyACM0</span></div>
        <div class="row" style="margin-top:14px">
          <button class="btn-green" onclick="sstMode('rx')">RX</button>
          <button class="btn-gold" onclick="sstMode('idle')">Idle</button>
        </div>
        <div style="margin-top:12px">
          <label>Send text on SST01 (TX)</label>
          <input id="sstmsg" type="text" value="HELLO FROM GALAMAD" spellcheck="false">
        </div>
        <div class="row">
          <button class="btn-gold" onclick="sstSend()">Send Text</button>
        </div>
        <div id="sstsgo" class="msg">SST01 idle — press RX to receive, or enter text + Send.</div>
        <div class="sig" style="margin-top:12px">
          <div class="label">SST01 RX / status</div>
          <pre id="sstlog" style="white-space:pre-wrap;font-size:11px;color:var(--mut);margin:0;max-height:150px;overflow:auto">(no data yet)</pre>
        </div>
      </div>
    </div>
  </div>
  <div class="footer">GALAMAD AEROSPACE &middot; GFSK Transceiver</div>
</div>
<script>
const CFG={max:64,sec:"6"};
function setRateText(f, r){
  document.querySelectorAll('[data-f]').forEach(e=>e.textContent=f);
  document.querySelectorAll('[data-r]').forEach(e=>e.textContent=r);
}
for(const e of document.querySelectorAll('[data-m]'))e.textContent=CFG.max;
for(const e of document.querySelectorAll('[data-s]'))e.textContent=CFG.sec;
async function jpost(url, body){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body||{})});
  return r.json();
}
async function refreshStatus(){
  try{
    const s = await (await fetch('/status')).json();
    const on=s.tx_running;
    document.getElementById('txdot').className='dot '+(on?'on':'off');
    document.getElementById('txstate').textContent=on?'ON':'idle';
    document.getElementById('stopbtn').disabled=!on;
    document.getElementById('txlive').classList.toggle('show',on);
    if(s.freq){
      document.getElementById('txfreq').textContent=(s.freq/1e6).toFixed(3);
      document.getElementById('rxfreq').textContent=(s.rx_freq/1e6).toFixed(3);
      document.getElementById('rxtune').textContent=(s.rx_tune/1e6).toFixed(3);
      if(document.activeElement!==document.getElementById('freq')) document.getElementById('freq').value=(s.freq/1e6).toFixed(3);
      if(document.activeElement!==document.getElementById('rxbox')) document.getElementById('rxbox').value=(s.rx_freq/1e6).toFixed(3);
    }
  }catch(e){}
}
async function send(){
  const m=document.getElementById('msg').value.trim();
  const out=document.getElementById('smsgo');out.className='msg';
  out.textContent='Configuring transmission: '+m+'…';
  // immediately hide TX animation (old TX is about to be killed)
  document.getElementById('txlive').classList.remove('show');
  document.getElementById('txstate').textContent='starting…';
  document.getElementById('txdot').className='dot';
  const r=await jpost('/send',{message:m});
  if(r.ok){
    // poll until TX is actually running (hackrf_transfer takes a moment to start)
    for(let i=0;i<8;i++){
      await new Promise(x=>setTimeout(x,500));
      const s=await (await fetch('/status')).json();
      if(s.tx_running){ out.className='msg ok'; out.textContent='Transmitting: '+r.message; refreshStatus(); return; }
    }
    out.className='msg ok'; out.textContent='Transmitting: '+r.message; refreshStatus();
  }else{out.className='msg err';out.textContent=r.error||'error';}
}
async function sendFile(){
  const inp=document.getElementById('file');
  if(!inp.files.length){ document.getElementById('fname').textContent='No file'; return; }
  const fd=new FormData(); fd.append('file', inp.files[0]);
  const out=document.getElementById('smsgo'); out.className='msg'; out.textContent='Uploading '+inp.files[0].name+'…';
  document.getElementById('fname').textContent=inp.files[0].name;
  try{
    const r=await (await fetch('/sendfile',{method:'POST',body:fd})).json();
    if(r.ok){
      document.getElementById('msg').value=r.message;
      out.className='msg ok'; out.textContent='Transmitting file '+r.filename+': '+r.message;
      for(let i=0;i<8;i++){ await new Promise(x=>setTimeout(x,500)); const s=await (await fetch('/status')).json(); if(s.tx_running){ refreshStatus(); return; } }
      refreshStatus();
    }else{ out.className='msg err'; out.textContent=r.error||'error'; }
  }catch(e){ out.className='msg err'; out.textContent='upload failed'; }
}
async function stopTx(){
  let r=await jpost('/stop');document.getElementById('smsgo').className='msg err';
  document.getElementById('smsgo').textContent='Transmission stopped';refreshStatus();
}
async function recv(){
  const btn=document.querySelector('button[onclick="recv()"]');
  if(btn) btn.disabled=true;
  const busy=document.getElementById('busy');
  const list=document.getElementById('rxlist');list.innerHTML='';
  busy.style.color='var(--acc)';busy.textContent='Capturing ~6 s… please wait…';
  let r;
  try{r=await jpost('/capture');}catch(e){ if(btn) btn.disabled=false; busy.textContent='capture failed';return;}
  if(r && r.error && String(r.error).includes('already running')){
    busy.textContent='A capture is in progress — retrying…';
    await new Promise(r=>setTimeout(r,1500));
    try{r=await jpost('/capture');}catch(e){ if(btn) btn.disabled=false; busy.textContent='capture failed';return;}
  }

  if(r.ok){
    busy.style.color='var(--ok)';busy.textContent='Done capturing ✓ — now decoding…';
    try{r=await jpost('/decode');}catch(e){busy.style.color='var(--err)';busy.textContent='decode failed';return;}
  }else{ if(btn) btn.disabled=false; busy.style.color='var(--err)';busy.textContent='Capture error: '+(r.error||'?');return;}
  busy.style.color='var(--acc)';busy.textContent='';
  if(r.metrics) showMetrics(r.metrics);
  if(!r.ok){list.innerHTML='<li style="color:var(--err)">Error: '+(r.error||'?')+'</li>';return;}
  if(r.count===0){list.innerHTML='<li style="color:var(--mut)">No packets decoded</li>'; if(btn) btn.disabled=false; return;}
  list.innerHTML='';
  for(const p of r.packets){
    const li=document.createElement('li');
    const badge='<span class="badge '+(p.crc_ok?'ok':'bad')+'">'+(p.crc_ok?'CRC OK':'CRC FAIL')+'</span>';
    li.innerHTML='<span>'+escapeHtml(p.text)+'</span><span>'+badge+'</span>';
    list.appendChild(li);
  }
}
function showMetrics(m){
  document.getElementById('satflag').textContent=m.saturating?'OVERLOAD — reduce gain':'';
  document.getElementById('satflag').style.display=m.saturating?'':'none';
  document.getElementById('lvl').innerHTML='RMS: <b>'+m.rms_dbfs+'</b> dBFS';
  document.getElementById('pk').innerHTML='Peak: <b>'+m.peak_dbfs+'</b> dBFS';
  // quality badge
  const q=document.getElementById('quality');
  q.textContent=m.quality;
  q.className='quality '+(m.quality==='Strong'?'strong':m.quality==='Fair'?'fair':m.quality==='Weak'?'weak':'none');
  // carrier info
  const ci=document.getElementById('carrierinfo');
  if(m.carrier_khz&&Math.abs(m.carrier_khz)>0.5){
    ci.textContent='Carrier offset: '+(m.carrier_khz>0?'+':'')+m.carrier_khz+' kHz';
  }else{ ci.textContent=''; }
  // meter bar: 0 dBFS ~100%, -40 dBFS ~0%
  let pct=Math.max(0,Math.min(100,100*(m.rms_dbfs+40)/40));
  document.getElementById('lvlbar').style.width=pct+'%';
  document.getElementById('lvlbar').style.background = m.saturating
    ? 'var(--err)' : 'linear-gradient(90deg,var(--green),var(--gold),var(--red))';
  // strongest peak
  const pk=document.getElementById('peaks');
  if(m.peak_freq_khz!==undefined&&Math.abs(m.peak_freq_khz)>0.5){
    pk.innerHTML='Strongest peak: <b>'+m.peak_freq_khz+' kHz</b> ('+m.peak_dbfs+' dBFS)';
  }else{ pk.innerHTML=''; }
}
async function checkSignal(){
  const busy=document.getElementById('busy');
  busy.style.color='var(--acc)';busy.textContent='Checking signal…';
  let r;
  try{r=await jpost('/signal');}catch(e){busy.style.color='var(--err)';busy.textContent='signal check failed';return;}
  busy.style.color='var(--acc)';busy.textContent='';
  if(r.metrics){ rm=document.getElementById('rxlist'); rm.innerHTML='';
    showMetrics(r.metrics); }
}
function escapeHtml(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
refreshStatus();setInterval(refreshStatus,2000);

// ---- SST01 mode control ----
async function refreshSst(){
  try{
    const s=await (await fetch('/sst01/status')).json();
    const dot=document.getElementById('sstdot'), st=document.getElementById('sststate'),
          pill=document.getElementById('sstpill'), go=document.getElementById('sstsgo');
    dot.className='dot '+(s.present?'on':'off');
    let label=(s.present && s.state)?s.state:((s.present)?'connected':'offline');
    st.textContent=label;
    if(pill) pill.classList.toggle('act', s.present && label!=='connected' && label!=='idle');
    if((s.rx_msgs&&s.rx_msgs.length)||(s.tx_msgs&&s.tx_msgs.length)){
      const log=[];
      (s.tx_msgs||[]).forEach(l=>log.push('SST TX: '+l));
      (s.rx_msgs||[]).forEach(l=>log.push('SST RX: '+l));
      document.getElementById('sstlog').textContent=log.join('\n')+'\n'+(s.last_state?'—— '+s.last_state:'');
    }else{
      document.getElementById('sstlog').textContent=s.last_state||(s.present?'SST01 connected':'SST01 not connected');
    }
    if(s.auto===true && go && go.textContent.indexOf('autonomous')<0 && go.className!=='msg ok' && go.className!=='msg err'){
      go.textContent=(label==='AUTO RX/TX')
        ?'Radio is alternating TX+RX on its own — the RX/TX buttons apply only to the command firmware.'
        :'Radio is running autonomous firmware — the RX/TX buttons apply only to the command firmware.';
    }
  }catch(e){}
}
async function sstMode(mode){
  const out=document.getElementById('sstsgo'); out.className='msg';
  out.textContent=mode==='rx'?'Switching SST01 to RX (receive packets)…':(mode==='tx'?'Switching SST01 to TX…':'Switching SST01 to idle…');
  const r=await jpost('/sst01/mode',{mode});
  if(r.ok){ out.className='msg ok'; out.textContent='SST01 now '+(r.mode||mode)+': '+(r.resp||''); }
  else{ out.className='msg err'; out.textContent=r.error||'error'; }
  refreshSst();
}
async function sstSend(){
  const t=document.getElementById('sstmsg').value.trim();
  const out=document.getElementById('sstsgo'); out.className='msg';
  out.textContent='SST01 TX: '+t+'…';
  const r=await jpost('/sst01/send',{text:t});
  if(r.ok){ out.className='msg ok'; out.textContent='Sent on SST01: '+r.text+' ('+(r.resp||'ok')+')'; }
  else{ out.className='msg err'; out.textContent=r.error||'error'; }
  refreshSst();
}
refreshSst();setInterval(refreshSst,1500);

// ---- device selectors + TX power/amp ----
async function loadDevices(){
  try{
    const s = await (await fetch('/devices')).json();
    const hf=document.getElementById('hfsel'), rx=document.getElementById('rxsel');
    hf.innerHTML='';
    const auto=document.createElement('option'); auto.value=''; auto.textContent='Default (auto)';
    hf.appendChild(auto);
    for(const ser of (s.hackrf||[])){
      const o=document.createElement('option'); o.value=ser; o.textContent='HackRF '+ser.slice(-6);
      hf.appendChild(o);
    }
    if(s.selected && s.selected.hackrf_serial) hf.value=s.selected.hackrf_serial;
    rx.innerHTML='';
    const rtl=s.rtl||[], hfs=s.hackrf||[];
    for(const i of rtl){
      const o=document.createElement('option'); o.value='rtl:'+i; o.textContent='RTL-SDR #'+i;
      rx.appendChild(o);
    }
    for(const ser of hfs){
      const o=document.createElement('option'); o.value='hackrf:'+ser; o.textContent='HackRF '+ser.slice(-6);
      rx.appendChild(o);
    }
    if(!rtl.length && !hfs.length){
      const o=document.createElement('option'); o.value=''; o.textContent='No receiver detected';
      rx.appendChild(o);
    }
    // restore current RX selection (only if the device is actually present)
    const sel=s.selected;
    let rxval;
    if(sel.rx_kind==='hackrf' && sel.hackrf_rx_serial){ rxval='hackrf:'+sel.hackrf_rx_serial; }
    else { rxval='rtl:'+(sel.rx_index>=0?sel.rx_index:0); }
    if(document.querySelector('#rxsel [value="'+rxval+'"]')) rx.value=rxval;
    updateRxGainSel(sel);
    hf.onchange=()=>jpost('/select',{hackrf_serial:hf.value||null});
    rx.onchange=(e)=>{
      if(String(e.target.value).startsWith('hackrf:')){
        jpost('/select',{rx_kind:'hackrf', hackrf_rx_serial:String(e.target.value).split(':')[1]}).then(()=>updateRxGainSel());
      }else{
        jpost('/select',{rx_kind:'rtl', rx_index:Number(String(e.target.value).split(':')[1])}).then(()=>updateRxGainSel());
      }
    };
    document.getElementById('rxlna').addEventListener('change',()=>jpost('/select',{rx_lna:Number(document.getElementById('rxlna').value)}));
    document.getElementById('rxvga').addEventListener('change',()=>jpost('/select',{rx_vga:Number(document.getElementById('rxvga').value)}));
    // disable Capture & Signal buttons when no receiver is actually available
    const hasRx = rtl.length > 0 || hfs.length > 0;
    document.querySelector('button[onclick="recv()"]').disabled = !hasRx;
    document.querySelector('button[onclick="checkSignal()"]').disabled = !hasRx;
  }catch(e){}
}
async function updateRxGainSel(sel){
  try{
    if(!sel) sel = await (await fetch('/devices')).json();
  }catch(e){ return; }
  const sx = (sel && sel.selected) ? sel.selected : (sel || {});
  const isHack = sx.rx_kind==='hackrf';
  document.getElementById('rxgainrow').style.display = isHack?'' : 'none';
  if(isHack){
    document.getElementById('rxlna').value=sx.rx_lna;
    document.getElementById('rxvga').value=sx.rx_vga;
  }
  // show the selected receiver name (buttons are already disabled when absent)
  let label;
  if(isHack){
    label = 'HackRF '+(sx.hackrf_rx_serial||'').slice(-6);
  }else{
    label = 'RTL-SDR #'+sx.rx_index;
  }
  document.getElementById('rxkind').textContent = label;
}
async function loadTxCfg(){
  try{
    const s=await (await fetch('/status')).json();
    const g=document.getElementById('txgain'); g.value=s.tx_gain;
    document.getElementById('txgainval').textContent=s.tx_gain;
    document.getElementById('amp').checked=s.amp;
    if(s.freq){ document.getElementById('freq').value=(s.freq/1e6).toFixed(3); }
    if(s.rx_freq){ document.getElementById('rxbox').value=(s.rx_freq/1e6).toFixed(3); }
  }catch(e){}
}
async function applyTxCfg(){
  const g=Number(document.getElementById('txgain').value)||0;
  const a=document.getElementById('amp').checked;
  document.getElementById('txgainval').textContent=g;
  await jpost('/txcfg',{tx_gain:g,amp:a});
  refreshStatus();
}
document.getElementById('txgain').addEventListener('input',()=>{
  document.getElementById('txgainval').textContent=document.getElementById('txgain').value;
});
document.getElementById('txgain').addEventListener('change',applyTxCfg);
document.getElementById('amp').addEventListener('change',applyTxCfg);
async function applyFreq(){
  const f=parseFloat(document.getElementById('freq').value);
  const r=parseFloat(document.getElementById('rxbox').value);
  if(isNaN(f)&&isNaN(r)){return;}
  const body={};
  if(!isNaN(f)) body.freq_mhz=f;
  if(!isNaN(r)) body.rx_freq_mhz=r;
  const out=await jpost('/freq',body);
  if(out.ok){
    document.getElementById('freq').value=(out.freq_hz/1e6).toFixed(3);
    document.getElementById('rxbox').value=(out.rx_freq_hz/1e6).toFixed(3);
    refreshStatus(); loadTxCfg();
  }
}
document.getElementById('freq').addEventListener('change',applyFreq);
document.getElementById('rxbox').addEventListener('change',applyFreq);
loadDevices(); setInterval(loadDevices,8000);
loadTxCfg();

// ---- link-rate preset (rate only — frequency independent) ----
async function loadPresets(){
  try{
    const s=await (await fetch('/presets')).json();
    const sel=document.getElementById('preset'); sel.innerHTML='';
    for(const p of s.presets){
      const o=document.createElement('option'); o.value=p.name; o.textContent=p.label;
      sel.appendChild(o);
    }
    sel.value=s.current;
    sel.onchange=async ()=>{
      await jpost('/preset',{name:sel.value});
      refreshStatus();
    };
  }catch(e){}
}
loadPresets();

</script>
</body></html>
"""


if __name__ == "__main__":
    # auto-detect: if no RTL-SDR but HackRFs exist, default RX to first HackRF
    _KNOWN_GOOD_HACKRF = "000000000000000016bc62dc2e4f52a7"  # bench-verified f52a7
    _rtl = _list_rtl()
    _hackrfs = _list_hackrf()
    if _hackrfs:
        _sel["rx_kind"] = "hackrf"
        # 2026-09-05: prefer the known-good f52a7 over the broken a864b
        _good = [s for s in _hackrfs if s.lstrip("0") != "285067dc2a2a864b"]
        _sel["hackrf_rx_serial"] = _good[0] if _good else _hackrfs[0]
    elif not _rtl and _KNOWN_GOOD_HACKRF:
        # enumeration is wedged (broken a864b blocks hackrf_info) — still use f52a7
        _sel["rx_kind"] = "hackrf"
        _sel["hackrf_rx_serial"] = _KNOWN_GOOD_HACKRF
    print("GALAMAD GFSK Transceiver UI")
    print("Open: http://localhost:5000/   (remote: http://<this-host>:5000/)")
    print("Ctrl-C to quit.")
    _apply_preset(_cur["name"])          # sync decoder knobs to default preset
    _tx["msg"] = DEFAULT_MSG             # shown as current message; not transmitting
    from waitress import serve
    serve(app, host="0.0.0.0", port=PORT)
