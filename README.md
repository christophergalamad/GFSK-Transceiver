# GALAMAD GFSK Transceiver

A browser-driven ground-segment app for a **GFSK UHF radio link**. With one or
two **HackRF One** sticks you can generate GFSK bursts, transmit them on air,
capture the RF and demodulate/decode the packets back to text — all from a web
page at `http://localhost:5000`.

It was built and validated for the GALAMAD Aerospace UHF link and also works as
a simple, cheap lab radio link (HackRF ↔ HackRF loopback).

> **TL;DR for the impatient**
> ```bash
> python3 -m venv .venv && . .venv/bin/activate
> pip install -r requirements.txt
> python3 gfsk_ui.py          # then open http://localhost:5000
> ```
> No radio attached? The app still starts; run the offline round-trip test to
> verify the DSP chain (`python3 tests/test_roundtrip.py`).

---

## 1. What is this?

- **Transmit**: writes your text as a GFSK packet (preamble + sync + payload +
  CRC-16), generates the IQ waveform, and plays it out of a HackRF via
  `hackrf_transfer` in a repeating loop.
- **Receive**: captures IQ from a HackRF (or RTL-SDR), finds bursts, locks the
  carrier per burst, demodulates GFSK and returns the decoded text with CRC
  validation and signal metrics.
- **Beacon card** (optional): reads live state from a serial-connected
  Si443x-class/Nucleo beacon so one page shows the whole link.

The packet format on air is:

```
[  128 × 0xAA preamble  ][  sync 0x2DD4  ][ payload 1–32 bytes ][ CRC-16 CCITT ]
```

- GFSK modulation, data rate 9.6 kbit/s (bench) or 200 kbit/s (sat) — see Presets.
- CRC = CRC-16 CCITT (poly `0x1021`, init `0x0000`) over the payload only.
- No length byte on air: the length is fixed per link (7 bytes for the classic
  beacon, or the exact length of your message for HackRF↔HackRF).

---

## 2. What you need

| Item | Needed for | Notes |
|------|-----------|-------|
| Linux, macOS or Windows (WSL works) | everything | Python 3.10+ |
| Python 3.10+ | everything | `python3 --version` |
| **HackRF One** | TX always; RX if used as receiver | one is enough to start; two = loopback test |
| RTL-SDR dongle | RX only (alternative to a 2nd HackRF) | optional |
| Antennas | on-air TX/RX | short-range tests work with a steel wire if you keep them close |

The core DSP is pure Python (numpy/scipy), so you can run the whole app **with
no radio at all** — requests that need hardware will just report "no device".

---

## 3. Installation

### 3.1 Python dependencies

```bash
git clone https://github.com/christophergalamad/GFSK-Transceiver.git
cd GFSK-Transceiver

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Only four packages: `flask`, `waitress`, `numpy`, `scipy`.

### 3.2 Radio drivers (for real RF)

Ubuntu/Debian:

```bash
sudo apt install hackrf rtl-sdr
hackrf_info          # should list your HackRF(s) and firmware version
```

If `hackrf_info` says it can't open the device, you need the udev rule for
non-root access (search your distro for "hackrf.rules", or run as root for a
quick check).

### 3.3 Verify the install without any radio

```bash
python3 tests/test_roundtrip.py
```

This generates GFSK IQ in memory and decodes it back — it passes only if the
modulator + demodulator + CRC all agree. If it passes, the app is healthy.

---

## 4. Run the app

```bash
source .venv/bin/activate
python3 gfsk_ui.py
# or: ./start.sh           (uses setsid, logs to ui.log)
# stop:  ./stop.sh         (also stops the background hackrf_transfer)

# open in your browser:
#   http://localhost:5000
```

You'll see the signal-strength panel and the TX/RX controls. If a HackRF is
plugged in, the app auto-detects it (you can pick which device transmits and
which receives).

---

## 5. First on-air test: HackRF ↔ HackRF

The recommended first RF test uses **two HackRFs**: radio A transmits your
message, radio B receives and decodes it. Keep them a few metres apart with a
little RF attenuation (`-x` gain) so the receiver doesn't clip.

1. Start the app (above), plug in both HackRFs.
2. In the **Receive** card pick the RX device; set **LNA** to `24` and **VGA**
   to `48`. In the **Transmit** card pick the TX device.
3. Set **TX freq** and **RX freq** both to the same channel, e.g. `437.000`.
   (See §6 for why RX tunes slightly below.)
4. In the **Transmit** box type a short message (e.g. `GALA152`, or any text
   up to 32 characters) and click **Send Text**.
5. Click **Capture & Decode** — it records ~6 s of RF and decodes it in one go
   (about 9 s total).
6. The **Packets** panel lists what was decoded, e.g.
   `GALA152  crc_ok:true  occurrences: N` together with the carrier offset
   (+100 kHz for a correctly tuned peer) and signal metrics.

A known-good bench recipe is:

```
TX: HackRF A, tx gain 40, amplifier ON      (= -x 40 -a 1)
RX: HackRF B, LNA 24, VGA 48                (= -l 24 -g 48)
Freq: TX 437.000  RX 437.000
```

That combination is what the development bench runs every day.

---

## 6. How the tuning works (read this before changing boxes)

- **TX freq** = the frequency your local transmitter radiates.
- **RX freq** = the frequency you *intend to hear*.
- The receiver RF chain is tuned **100 kHz below** the RX box value on purpose
  (`tune = RX freq − 100 kHz`). That keeps the in-phase/quadrature DC spike out
  of the passband: a peer transmitting exactly on the RX-box frequency therefore
  appears **+100 kHz** in the spectrum — exactly where the decoder expects it.

So for two identical radios: type the same value in both boxes and it "just
works". Only adjust when a specific peer radiator has a big local-oscillator
offset (some units are off by a few hundred kHz — then set the RX box to the
frequency that radio actually transmits on).

---

## 7. Shipping presets

| Preset | Data rate | Deviation | Use |
|--------|-----------|-----------|-----|
| `bench` | 9.6 kbit/s | ~4.8 kHz | Lab/bench, best demod robustness |
| `sat`   | 200 kbit/s | ~50 kHz  | Higher-rate / satellite-style link |

Switch with the preset dropdown in the UI, or `POST /preset {"name":"sat"}`.
Python note: 9.6 kbit/s at 2.4 Msps = 125 samples/symbol (sps); 200 kbit/s = 12
sps. Solid integer samples-per-symbol values demodulate cleanly.

---

## 8. CLI reference (no browser needed)

Generate IQ for a message:

```bash
python3 generate_gfsk_iq.py --text "GALA152" --out hello.iq \
  --sample-rate 2400000 --data-rate 9600 --deviation 4800 \
  --freq 437000000 --digital-gain 0.5
```

Transmit it (loop, amplifier on, TX gain 40 dB):

```bash
hackrf_transfer -t hello.iq -f 437000000 -s 2400000 -a 1 -x 40 -p 1 -R
```

Capture 6 s of RX on HackRF B, tuned 100 kHz below the wanted channel:

```bash
hackrf_transfer -r cap.iq -d <serial B> -f 436900000 -s 1200000 \
  -n 7200000 -a 0 -l 24 -g 48
```

Decode:

```bash
python3 decode_sst01.py cap.iq --fs 1200000 --payload-len 7
python3 decode_sst01.py cap.iq --fs 1200000 --payload-len 25   # longer message
```

> `--payload-len` must match the transmitted message length. A wrong length
> yields "0 packets" but a *fast* answer. The web app sets this for you
> automatically (it uses the length of whatever you last sent).

---

## 9. Hardware: Si443x-class / Nucleo beacon (optional)

The repo ships an optional companion transmitter: a **Si443x-class** radio
driven from an STM32 **Nucleo G474RE** by bit-banged SPI at 434 MHz. Firmware
sketches live in `nucleo_firmware/` (sst01_test, sst_fixlen, sst_rx_tx). Its
on-air packet is the same fixed-length format the decoder reads — 7 payload
bytes, sync `0x2DD4`, CRC-16 CCITT — so the app decodes the beacon as easily
as a HackRF packet.

The bench wiring and bring-up runbook are detailed **inside the repo's
`AGENTS.md`** (wiring table, register state, do-not-do list). A quick checklist
if you build one:

- Power the radio at **3.3 V** (never 5 V).
- Both SDN pins tied together and held low to leave shutdown.
- bit-bang SPI Mode 0 at ≤ 1 MHz SCLK (proven at 10 kHz and at 1 MHz).
- RXON/TXON both high during register access.

---

## 10. Troubleshooting

| Symptom | Likely cause → fix |
|---------|--------------------|
| `hackrf_info` finds nothing | USB/udev; try another USB port, run with sudo once to confirm |
| "Resource busy" or garbage captures | another app (gqrx/HackRF tools) holds the radio — close *and* kill it |
| App says OVERLOAD / saturating | receiver gain too high for a strong/close transmitter → lower LNA/VGA (e.g. LNA 0 / VGA 8 for an adjacent beacon) |
| Strong carrier but **0 packets** | TX gain too low → use **`-x ≥ 30` with amplifier on**; or your `--payload-len`/message length differ |
| Carrier seen but at the wrong offset | that radio's LO is off; set the RX box to the frequency it really transmits on |
| Audio-style slow decode | don't omit `--payload-len` on noisy captures (an auto‑scan of all 32 lengths is slow); the app never has this problem |
| Settings reset after restart | the app keeps its config in memory only — re-select devices/gains in the UI (u.i. by design) |
| Message decoded as garbage | wrong message length, or CRC failing due to clipping — lower TX gain / add attenuation |

The two gain knobs that trip everyone up:

- **TX power** on a HackRF is the **`-x` (TX VGA) gain, 0–47 dB**, not the RX
  `-g`. `-g` only affects receiving. Radiation without `-x ≥ ~30` + amp is
  essentially absent.
- **RX gain** without clipping: start at **LNA 24 / VGA 48** for long-range, and
  back **down** for strong/nearby signals so the ADC doesn't clip.

---

## 11. HTTP API (quick map)

Used by the UI; handy for scripts.

| Method & path | Purpose |
|---------------|---------|
| `GET /status` | devices, freq, gains, TX running |
| `GET /devices` | detected HackRF/RTL list |
| `POST /select` | pick TX device, RX device + RX gains |
| `POST /freq` | set TX freq / RX freq (`freq_mhz`, `rx_freq_mhz`) |
| `POST /txcfg` | tx_gain (0–47), amp on/off |
| `POST /preset` | switch bench/sat |
| `POST /send` | transmit `message` (≤ 64 B) in a loop |
| `POST /sendfile` | play an uploaded `.iq` in a loop |
| `POST /stop` | stop the TX loop |
| `POST /capture` | capture RX to disk (`data/`) |
| `POST /decode` | decode the last capture |
| `POST /signal` | signal metrics for the last capture |
| `GET /spectrum` | raw FFT power array |
| `GET/` `POST /sst01/…` | optional Si443x-class/Nucleo card state + commands |

---

## 12. Project layout

```
GFSK-Transceiver/
├── gfsk_ui.py            Flask web app (UI + API, background SDR control)
├── generate_gfsk_iq.py   GFSK modulator → int8 IQ file
├── decode_sst01.py       burst finder + per-burst carrier lock + CRC decode
├── decode_gfsk.py        (older demodulator; superseded by decode_sst01.py)
├── config.py             RF params: rates, presets, gains, device mapping
├── requirements.txt
├── start.sh / stop.sh    setsid start + pkill stop (also stops the SDR child)
├── nucleo_firmware/      optional Si443x-class/Nucleo sketches (.ino)
└── tests/
    └── test_roundtrip.py offline modulator→demodulator regression test
```

---

## 13. Notes for developers

- `tests/test_roundtrip.py` is the safety net — run it before and after changes
  to the DSP chain.
- Keep payloads ≤ 32 bytes if a peer needs to *decode* them (the decoder's
  validated frame-length ceiling); TX itself accepts up to 64 bytes.
- The web app is the maintained front end; the Dart-style CLI tools are thin
  wrappers over the same Python modules.