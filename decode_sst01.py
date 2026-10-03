"""Offline GFSK demodulator / decoder for the EXA SST01 (Si443x family, DT=0x08).

SST01 fixed-packet-length PH mode transmits, per burst:

    <Preamble 0xAA> 0x2D 0xD4  <payload> <CRC-16 CCITT (payload only)>

with NO length byte on air (packet length = register 0x3E), data MSB first,
CRC-16 CCITT (poly 0x1021, init 0x0000) over the payload ONLY (crcdonly=1),
sent big-endian. Verified on-air (this project):

    [0xAA preamble][0x2D 0xD4] 'GALAMAD' [0x00][0x32]

Pipeline: IQ -> FM discriminator -> Gaussian matched filter -> per-phase
sync (0x2D D4) search -> fixed-length payload extraction -> CRC verify.

The demod is tolerant of a burst carrier at *any* IF offset: each burst is
located by mean-subtracted power and mixed to baseband using its own measured
carrier, so sparse / intermittent TX bursts decode reliably (the old
decode_gfsk.carrier_offset only looks at the first ~27 ms and mislocks on
sparse bursts).

Exposes a drop-in interface compatible with the GALAMAD-GFSK-Transceiver app:
    read_iq, carrier_offset, lowpass, fm_demodulate,
    decode(path, fs, lo_center=None, keep_bad=False) -> [(payload, crc_ok,
        crc_calc, crc_rx, phase[, n])...]
    decode_majority(path, fs, lo_center=None) -> same, majority-voted
"""

import numpy as np
from scipy.signal import firwin, lfilter, oaconvolve

DATA_RATE = 9600          # SST01/PHY bps (matches sst_fixlen.ino)
SST_DATA_RATE = 9600      # internal fixed rate; immune to app preset overwrite
SYNC_WORD = 0x2DD4
MAX_PAYLOAD_LEN = 32      # largest fixed payload we will try to validate via CRC
DEFAULT_PAYLOAD_LEN = 7   # 'GALAMAD'
PREAMBLE_BITS = 8
SYNC_PATTERN = [1, 0] * 40
FREQ_DEVIATION = 8000     # nominal sign-modulation deviation (Hz)


def crc16_ccitt(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def read_iq(path: str, signed: bool = True) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    n = len(raw) // 2
    i = raw[0::2].astype(np.float64)
    q = raw[1::2].astype(np.float64)
    # HackRF captures are SIGNED int8 (-128..127); RTL-SDR captures are
    # UNSIGNED 8-bit centered on 127.5. The app passes `signed` explicitly
    # from the selected RX device. Default to signed (legacy/HackRF).
    if signed:
        # HackRF: signed int8 centered on 0
        i = np.where(i >= 128, i - 256, i) / 128.0
        q = np.where(q >= 128, q - 256, q) / 128.0
    else:
        # RTL-SDR: unsigned 8-bit centered on 127.5
        i = (i - 127.5) / 128.0
        q = (q - 127.5) / 128.0
    return i + 1j * q


def _peak_freq(iq: np.ndarray, fs: float, lo_center: float, width: float):
    """Dominant spectral peak in [lo_center-width, lo_center+width] (Hz)."""
    n = min(len(iq), 1 << 16)
    seg = iq[:n] - iq[:n].mean()
    w = np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs))
    mask = (np.abs(f - lo_center) < width) & (np.abs(f) > 1500.0)
    if not mask.any():
        return 0.0, 0.0
    p = np.where(mask, w, 0.0)
    med = float(np.median(w[mask]))
    pk = float(p.max())
    return float(f[np.argmax(p)]), pk / med if med else 0.0


def carrier_offset(iq: np.ndarray, fs: float, lo_center: float = 0.0,
                   width: float = 150_000.0) -> float:
    """Estimate RF carrier offset (Hz) for the metrics panel.

    On sparse SST01 bursts a single global estimate is unreliable; still
    return the dominant peak (used only for display)."""
    fc, ratio = _peak_freq(iq, fs, lo_center, width)
    return fc if ratio >= 3.0 else 0.0


def gaussian_filter(bt: float, sps: float, span: int = 2) -> np.ndarray:
    nt = int(2 * span * sps) + 1
    t = np.arange(nt) / sps - span
    alpha = np.sqrt(2 * np.log(2)) / bt
    h = np.sqrt(2 * np.pi) / alpha * np.exp(-2 * (np.pi * t / alpha) ** 2)
    return h / np.sum(h)


def lowpass(iq: np.ndarray, fs: float, cutoff: float = 25000.0) -> np.ndarray:
    taps = firwin(201, cutoff / (fs / 2))
    return lfilter(taps, 1.0, iq)


def fm_demodulate(iq: np.ndarray, fs: float) -> np.ndarray:
    diff = iq[1:] * np.conj(iq[:-1])
    freq = np.angle(diff)
    # Scale generously; bit slicing is polarity-based, absolute dev not critical.
    scale = fs / (2 * np.pi * 8000)
    return np.clip(freq * scale, -2.0, 2.0)


def bits_to_bytes(bits):
    out = []
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | int(bits[i + j])
        out.append(byte)
    return bytes(out)


MAX_BURST_MS = 250.0  # cap processed burst length: a saturated capture would
                       # merge the whole window into one "burst" and _demod_burst
                       # would FIR+oaconvolve millions of samples for minutes.


def find_bursts(iq: np.ndarray, fs: float, min_dur_ms: float = 8.0):
    win = int(fs * 0.001)
    pw = np.array([np.mean(np.abs(iq[i:i + win]) ** 2)
                   for i in range(0, len(iq) - win, win)])
    # Noise-aware threshold: median is the noise floor; detect bursts well above
    # it. A fixed median*5 misses weak-but-real bursts (measured 1.75x on a
    # 5 m HackRF link), so scale by the robust dispersion (MAD) instead and
    # keep a floor of ~1.6x the median for strong signals.
    med = float(np.median(pw))
    mad = float(np.median(np.abs(pw - med)))
    thr = med + max(8.0 * mad, 0.6 * med)
    hi = pw > thr
    runs = []
    in_r = False
    for i, v in enumerate(hi):
        if v and not in_r:
            st = i
            in_r = True
        elif not v and in_r:
            runs.append((st * 0.001, i * 0.001))
            in_r = False
    if in_r:
        runs.append((st * 0.001, len(pw) * 0.001))
    out = []
    for (s, e) in runs:
        if (e - s) * 1000 < min_dur_ms:
            continue
        if (e - s) * 1000 <= MAX_BURST_MS:
            out.append((s, e))
        else:
            n = int(np.ceil((e - s) * 1000 / MAX_BURST_MS))
            step = (e - s) / n
            for i in range(n):
                a = s + i * step
                b = min(s + (i + 1) * step, e)
                if (b - a) * 1000 >= min_dur_ms:
                    out.append((a, b))
    return out


def _demod_burst(iq: np.ndarray, fs: float, t0: float, t1: float, sps: int,
                 cutoff: float = None):
    seg = iq[int(t0 * fs):int(t1 * fs)]
    seg = seg - seg.mean()
    w = np.abs(np.fft.fftshift(np.fft.fft(seg, 1 << 14))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(1 << 14, 1 / fs))
    fc = f[np.abs(w * (np.abs(f) > 4000)).argmax()]
    if cutoff is None:
        # scale the low-pass to the link bandwidth: deviation + data rate
        # (Carson-ish). Keep the legacy 25 kHz floor for the narrow 9.6k PHY.
        cutoff = max(25000.0, FREQ_DEVIATION + DATA_RATE)
        cutoff = min(cutoff, fs / 2 * 0.9)
    t = np.arange(len(seg)) / fs
    bb = seg * np.exp(-1j * 2 * np.pi * fc * t)
    bb = lfilter(firwin(201, cutoff / (fs / 2)), 1.0, bb)
    fr = np.angle(bb[1:] * np.conj(bb[:-1])) * fs / (2 * np.pi)
    fr = fr - np.median(fr)
    fr = oaconvolve(fr, gaussian_filter(0.5, sps), mode="same")
    fr = fr - np.median(fr)
    return fr


def _scan(iq: np.ndarray, fs: float, keep_bad: bool = False,
          fixed_lens=None):
    """Decode all fixed-length packets (payload_len auto-detected by CRC)."""
    sps = int(round(fs / DATA_RATE))
    sync_bits = [(SYNC_WORD >> i) & 1 for i in range(15, -1, -1)]
    tpl = np.array([2.0 * b - 1.0 for b in sync_bits], dtype=np.float64)
    if fixed_lens is None:
        fixed_lens = list(range(1, MAX_PAYLOAD_LEN + 1))
    result = {}
    for t0, t1 in find_bursts(iq, fs):
        fr = _demod_burst(iq, fs, t0, t1, sps)
        for phase in range(sps):
            sym = fr[phase::sps]
            if len(sym) <= len(tpl):
                continue
            _scale = float(sym.std())
            if _scale > 0:
                corr = np.correlate(sym / _scale, tpl, mode="valid")
            else:
                corr = np.correlate(sym, tpl, mode="valid")
            _thr = len(tpl) * 0.45
            for j in np.where(np.abs(corr) >= _thr)[0].tolist():
                is_pos = corr[j] > 0
                polarity = 1.0 if is_pos else -1.0
                ds = j + len(tpl)
                for plen in fixed_lens:
                    total = plen + 2
                    if len(sym) - ds < total * 8:
                        continue
                    bits = (sym[ds:ds + total * 8] * polarity > 0).astype(np.int8)
                    byts = bits_to_bytes(bits)
                    payload = byts[:plen]
                    if sum(1 for b in payload if 0x20 <= b <= 0x7E) / plen < 0.7:
                        continue
                    crc_rx = (byts[plen] << 8) | byts[plen + 1]
                    crc_calc = crc16_ccitt(payload)
                    crc_ok = (crc_rx == crc_calc) and crc_calc != 0
                    if not crc_ok and not keep_bad:
                        continue
                    key = bytes(payload)
                    cur = result.get(key)
                    if cur is None:
                        result[key] = [payload, crc_ok, crc_calc, crc_rx,
                                       phase, 1]
                    else:
                        cur[1] = cur[1] or crc_ok
                        cur[5] += 1
    out = [tuple(v) for v in result.values()]
    if not keep_bad:
        valid = [p for p in out if p[1]]
        strong = [p for p in out if p[1] and p[5] >= 2]
        if strong:
            out = strong
        elif valid:
            best = max(valid, key=lambda p: p[5])
            out = [best]
    return out


def decode(path: str, fs: float, lo_center: float = None, keep_bad: bool = False,
           payload_len: int = None, data_rate: float = None, signed: bool = True):
    global DATA_RATE
    if data_rate:
        DATA_RATE = float(data_rate)
    iq = read_iq(path, signed=signed)
    iq = iq - iq.mean()
    fixed_lens = [payload_len] if payload_len else None
    return _scan(iq, fs, keep_bad=keep_bad, fixed_lens=fixed_lens)


def decode_auto(path: str, fs: float, signed: bool = True, max_len: int = 64):
    """Receive fallback: CRC-search every fixed length 1..max_len.

    Used when the peer's payload length is unknown (the on-air frame carries no
    length byte). _scan evaluates all candidate lengths in a single demod pass
    and, when any packet repeats (n >= 2), keeps only those — which suppresses
    the CRC false positives a wide length search would otherwise admit.
    """
    iq = read_iq(path, signed=signed)
    iq = iq - iq.mean()
    return _scan(iq, fs, fixed_lens=list(range(1, max_len + 1)))


def decode_majority(path: str, fs: float, lo_center: float = None,
                    payload_len: int = None, signed: bool = True):
    # Cap to a single payload length: passing None makes decode() scan all
    # 32 lengths (lengths auto-detect) which is pathologically slow on a
    # noisy capture (~40s+). Majority-vote needs only the one fixed length.
    try:
        pkts = decode(path, fs, lo_center=lo_center, keep_bad=True,
                      payload_len=payload_len, signed=signed)
    except Exception:
        return []
    if not pkts:
        return []
    valid = [p for p in pkts if p[1]]
    pool = valid if valid else pkts
    best = max(pool, key=lambda p: p[5])
    n = best[5]
    if not valid and n < 2:
        return []
    return [(best[0], best[1], best[2], best[3], best[4], n)]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Decode SST01 GFSK from int8 IQ")
    ap.add_argument("file")
    ap.add_argument("--fs", type=float, default=2_400_000)
    ap.add_argument("--payload-len", type=int, default=DEFAULT_PAYLOAD_LEN)
    ap.add_argument("--rate", type=float, default=None,
                    help="data rate in bps (default: %d)" % globals()["DATA_RATE"])
    args = ap.parse_args()
    iq = read_iq(args.file)
    iq = iq - iq.mean()
    if args.rate:
        DATA_RATE = float(args.rate)
    print(f"Reading IQ... {args.file}  ({len(iq)} samples @ {args.fs/1e3:.0f} kHz, {DATA_RATE:.0f} bps)")
    pkts = _scan(iq, args.fs, fixed_lens=[args.payload_len])
    print(f"{len(pkts)} unique packet(s):")
    for p in pkts:
        payload, crc_ok, crc_calc, crc_rx, phase, n = p
        tag = "  <<< CONFIRMED" if crc_ok else "  (no-CRC candidates)"
        print(f"  n={n}  crc_ok={crc_ok}  crc(rx=0x{crc_rx:04X} calc=0x{crc_calc:04X})"
              f"  hex={payload.hex()}  text=\"{payload.decode('ascii','replace')}\"{tag}")
    return 0 if any(p[1] for p in pkts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
