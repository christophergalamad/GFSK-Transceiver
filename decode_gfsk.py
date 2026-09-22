#!/usr/bin/env python3
"""Offline GFSK demodulator / decoder for GALAMAD UHF Si4463 packets.

Pipeline: RTL-SDR raw int8 IQ -> carrier-lock (find dominant carrier, mix to
baseband) -> LPF -> FM discriminator -> Gaussian matched filter -> per-sync
phase/polarity scan -> 0x2DD4 sync search -> bit slice -> CRC-16 CCITT.

Only CRC-valid, mostly-printable payloads are reported (this is a text /
telemetry demoonstrator).

Usage:
    python decode_gfsk.py rx.iq --fs 1200000
"""

import argparse
import sys

import numpy as np
from scipy.signal import firwin, lfilter, oaconvolve

import config

DATA_RATE = config.DATA_RATE
FREQ_DEVIATION = config.FREQ_DEVIATION
SYNC_WORD = config.SYNC_WORD
MAX_PACKET_LEN = config.MAX_PACKET_LEN
SYNC_PATTERN = [1, 0] * 40             # 80 bits of the 0xAA preamble


def crc16_itu_t(data: bytes) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc


def read_iq(path: str) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.int8)
    n = len(raw) // 2
    return raw[0::2].astype(np.float64) / 128.0 + 1j * raw[1::2].astype(np.float64) / 128.0


def carrier_offset(iq: np.ndarray, fs: float,
                   lo_center: float = 0.0, width: float = 90_000.0) -> float:
    """Power-weighted spectral centroid of the carrier band (+-width of
    lo_center, excluding DC). Centroids are robust for wideband GFSK; the
    band is centered on the expected IF so the RTL-SDR image doesn't cancel
    out the true carrier."""
    n = min(len(iq), 1 << 16)
    seg = iq[:n] - iq[:n].mean()
    w = np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(n, 1.0 / fs))
    mask = (np.abs(f - lo_center) < width) & (np.abs(f) > 1500.0)
    if not mask.any():
        return 0.0
    p = np.where(mask, w, 0.0)
    if float(p.max()) < float(np.median(w[mask])) * 3.0:   # not a real carrier
        return 0.0
    return float(np.sum(f * p) / np.sum(p))


def lowpass(iq: np.ndarray, fs: float, cutoff: float = None) -> np.ndarray:
    if cutoff is None:
        cutoff = config.RX_LOWPASS_CUTOFF
    return lfilter(firwin(201, cutoff / (fs / 2)), 1.0, iq)


def fm_demodulate(iq: np.ndarray, fs: float) -> np.ndarray:
    diff = iq[1:] * np.conj(iq[:-1])
    freq = np.angle(diff)
    return np.clip(freq * fs / (2 * np.pi * FREQ_DEVIATION), -2.0, 2.0)


def gaussian_filter(bt: float, sps: float, span: int = 2) -> np.ndarray:
    nt = int(2 * span * sps) + 1
    t = np.arange(nt) / sps - span
    alpha = np.sqrt(2 * np.log(2)) / bt
    h = np.sqrt(2 * np.pi) / alpha * np.exp(-2 * (np.pi * t / alpha) ** 2)
    return h / np.sum(h)


def bits_to_bytes(bits: list) -> bytes:
    out = []
    for i in range(0, len(bits) - 7, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        out.append(byte)
    return bytes(out)


def find_packets(freq: np.ndarray, fs: float, keep_bad: bool = False):
    """Decode packets (per-sync phase/polarity scan).

    keep_bad=False: only CRC-valid, mostly-printable payloads are returned.
    keep_bad=True : every candidate payload is returned (CRC flag set), so a
    caller can majority-vote a repeated beacon even when the bench RF flips
    ~1 CRC bit per packet.
    """
    freq = freq - np.median(freq)
    sps = int(round(fs / DATA_RATE))
    freq = oaconvolve(freq, gaussian_filter(config.BT, sps, span=2), mode="same")
    freq = freq - np.median(freq)

    sync_bits = [(SYNC_WORD >> i) & 1 for i in range(15, -1, -1)]
    tpl = np.array([2.0 * b - 1.0 for b in (SYNC_PATTERN + sync_bits)], dtype=np.float64)

    if len(freq) // sps < 100:
        return []

    from scipy.signal import find_peaks
    max_bits = (1 + MAX_PACKET_LEN + 2) * 8
    result = {}
    for phase in range(sps):
        sym = freq[phase::sps]
        if len(sym) <= len(tpl):
            continue
        corr = np.correlate(sym, tpl, mode="valid")
        ac = np.abs(corr)
        if ac.size == 0:
            continue
        thr = max(len(tpl) * 0.15, ac.max() * 0.7)
        for peak_sym in find_peaks(ac, height=thr, distance=8)[0]:
            is_pos = corr[peak_sym] > 0
            data_start_sym = peak_sym + len(tpl)
            centers = (phase + (data_start_sym + np.arange(max_bits)) * sps).astype(int)
            centers = centers[(centers >= 0) & (centers < len(freq))]
            polarity = 1.0 if is_pos else -1.0
            raw_bits = [1 if freq[c] * polarity > 0 else 0 for c in centers]
            if len(raw_bits) < 24:
                continue
            raw_bytes = bits_to_bytes(raw_bits)
            payload_len = raw_bytes[0]
            if payload_len == 0 or payload_len > MAX_PACKET_LEN:
                continue
            total = 1 + payload_len + 2
            if len(raw_bytes) < total:
                continue
            pkt = raw_bytes[:total]
            payload = pkt[1:1 + payload_len]
            crc_rx = (pkt[-2] << 8) | pkt[-1]
            crc_calc = crc16_itu_t(pkt[:1 + payload_len])
            crc_ok = (crc_rx == crc_calc) and crc_calc != 0
            if sum(1 for b in payload if 0x20 <= b <= 0x7E) / len(payload) < 0.7:
                continue
            key = bytes(payload)
            if not crc_ok and not keep_bad:
                continue
            cur = result.get(key)
            if cur is None:
                result[key] = (payload, crc_ok, crc_calc, crc_rx, phase)
            elif crc_ok and not cur[1]:
                result[key] = (payload, True, crc_calc, crc_rx, phase)
    return list(result.values())


def decode(path: str, fs: float, lo_center: float = None, keep_bad: bool = False):
    if lo_center is None:
        lo_center = config.RX_LO_OFFSET      # carrier expected at +100 kHz IF
    iq = read_iq(path)
    fc = carrier_offset(iq, fs, lo_center=lo_center)
    if abs(fc) > 20_000.0:
        iq = iq * np.exp(-1j * 2 * np.pi * fc * np.arange(len(iq)) / fs)
    iq = lowpass(iq, fs)
    return find_packets(fm_demodulate(iq, fs), fs, keep_bad=keep_bad)


def decode_majority(path: str, fs: float, lo_center: float = None):
    """Decode a repeated beacon and return the most-common payload.

    Si4463 link packets repeat; the bench flips ~1 CRC bit per packet, so a
    strict decode yields 0. We only trust a payload that is recovered by >=2
    independent packets (a repeated message), flagging whether at least one
    instance CRC-validated. Single-shot decodes (noise) are ignored."""
    from collections import Counter
    pkts = decode(path, fs, lo_center=lo_center, keep_bad=True)
    if not pkts:
        return []
    counts = Counter(bytes(p[0]) for p in pkts)
    best_key, n = counts.most_common(1)[0]
    if n < 2:
        return []                      # no repeated/corroborated payload
    best = [p for p in pkts if bytes(p[0]) == best_key][0]
    any_crc = any(p[1] for p in pkts if bytes(p[0]) == best_key)
    return [(best[0], any_crc, best[2], best[3], best[4], n)]


def main():
    ap = argparse.ArgumentParser(description="Decode GALAMAD GFSK from RTL-SDR IQ")
    ap.add_argument("file")
    ap.add_argument("--fs", type=float, default=config.RX_SAMPLE_RATE)
    ap.add_argument("--lo-offset", type=float, default=0)
    args = ap.parse_args()

    print(f"Reading {args.file} @ {args.fs/1e3:.0f} kHz")
    packets = decode(args.file, args.fs, args.lo_offset)
    print(f"Decoded {len(packets)} packet(s).\n")
    for payload, crc_ok, crc_calc, crc_rx, phase in packets:
        txt = payload.decode("ascii", errors="replace")
        print(f"  >>> {'OK' if crc_ok else 'CRC-FAIL'} phase={phase} "
              f"crc=0x{crc_rx:04X} len={len(payload)} text=\"{txt}\"")
    return 0 if packets else 1


if __name__ == "__main__":
    sys.exit(main())
