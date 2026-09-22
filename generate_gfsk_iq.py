#!/usr/bin/env python3
"""Generate a HackRF-compatible GFSK IQ file (GALAMAD UHF / Si4463 format).

Modulation (kensat-compatible Si4463 packet format):
    [0xAA preamble][sync 0x2DD4][len 1B][payload <=64B][CRC-16 CCITT 2B]

On-air spec (see config.py):
    437.0 MHz, 200 kbps, +/-50 kHz deviation, BT=0.5 (GFSK)

HackRF transports INT8 IQ (I,Q interleaved). The composite file holds
`packets` packets separated by `gap_ms` of silence; stream with
`hackrf_transfer -t file -R` to loop it.

Usage:
    python generate_gfsk_iq.py --text "HELLO" --out gfsk.iq
"""

import argparse
import struct

import numpy as np

import config

# Runtime-overridable link params (default from config; --data-rate etc. override)
DATA_RATE = config.DATA_RATE
FREQ_DEVIATION = config.FREQ_DEVIATION
CENTER_FREQ_HZ = config.CENTER_FREQ_HZ
SAMPLE_RATE = config.TX_SAMPLE_RATE
SAMPLES_PER_SYMBOL = SAMPLE_RATE / DATA_RATE

CALLSIGN = config.DEFAULT_MESSAGE.split()[1] if len(config.DEFAULT_MESSAGE.split()) > 1 else "GALAMAD"


def crc16_itu_t(data: bytes) -> int:
    """CRC-16 CCITT (poly 0x1021, seed 0x0000) - matches Si4463."""
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


def bytes_to_bits_msb(data: bytes) -> np.ndarray:
    bits = []
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)
    return np.array(bits, dtype=np.float64)


def build_packet_bits(payload: bytes) -> np.ndarray:
    if len(payload) > config.MAX_PACKET_LEN:
        raise ValueError(f"Payload too long: {len(payload)} > {config.MAX_PACKET_LEN}")
    preamble = bytes([0xAA] * config.PREAMBLE_BYTES)
    sync = struct.pack(">H", config.SYNC_WORD)
    # SST01 fixed-packet-length format (matches decode_sst01.py / sst_fixlen.ino):
    # NO length byte on air (length = register 0x3E); CRC-16 CCITT over the
    # payload ONLY (crcdonly=1), sent big-endian.
    crc = crc16_itu_t(payload)
    crc_bytes = struct.pack(">H", crc)
    return bytes_to_bits_msb(preamble + sync + payload + crc_bytes)


def gaussian_filter(bt: float, samples_per_symbol: float, span: int = 4) -> np.ndarray:
    n_taps = max(int(2 * span * samples_per_symbol) + 1, 1)
    t = np.arange(n_taps) / samples_per_symbol - span
    alpha = np.sqrt(2 * np.log(2)) / bt
    h = np.sqrt(2 * np.pi) / alpha * np.exp(-2 * (np.pi * t / alpha) ** 2)
    return h / np.sum(h)


def gfsk_modulate(bits: np.ndarray) -> np.ndarray:
    """Return complex GFSK waveform for a bit array."""
    nrz = 2.0 * bits - 1.0
    sps = float(SAMPLES_PER_SYMBOL)
    n_out = int(round(len(nrz) * sps))
    # Zero-order-hold upsample: each sample takes the value of the symbol it
    # falls within (correct GFSK stair-step NRZ, works for non-integer sps).
    sym_idx = np.minimum((np.arange(n_out) / sps).astype(np.int64), len(nrz) - 1)
    upsampled = nrz[sym_idx]
    filtered = np.convolve(upsampled, gaussian_filter(config.BT, sps), mode="same")
    phase = 2 * np.pi * FREQ_DEVIATION * np.cumsum(filtered) / SAMPLE_RATE
    return np.exp(1j * phase)


def to_int8_iq(iq: np.ndarray, digital_gain: float = 0.9) -> np.ndarray:
    iq = iq * digital_gain
    i = np.clip(np.real(iq) * 127.0, -128, 127).astype(np.int8)
    q = np.clip(np.imag(iq) * 127.0, -128, 127).astype(np.int8)
    out = np.empty(2 * len(i), dtype=np.int8)
    out[0::2] = i
    out[1::2] = q
    return out


def build_composite(packets: int, gap_ms: float, digital_gain: float = 0.9,
                    text: str = None) -> np.ndarray:
    gap_samples = int(SAMPLE_RATE * gap_ms / 1000)
    segments = []
    for i in range(packets):
        payload = text.encode("ascii") if text is not None else f"{CALLSIGN} {i:04d}".encode("ascii")
        i8 = to_int8_iq(gfsk_modulate(build_packet_bits(payload)), digital_gain)
        segments.append(i8)
        segments.append(np.zeros(2 * gap_samples, dtype=np.int8))
    return np.concatenate(segments)


def main():
    ap = argparse.ArgumentParser(description="Generate GFSK IQ for HackRF")
    ap.add_argument("--out", default="gfsk.iq", help="output IQ file")
    ap.add_argument("--packets", type=int, default=30, help="packets per loop")
    ap.add_argument("--gap-ms", type=float, default=250, help="silence gap ms")
    ap.add_argument("--sample-rate", type=int, default=config.TX_SAMPLE_RATE,
                    help="IQ sample rate Hz (HackRF TX requires >= 2000000)")
    ap.add_argument("--digital-gain", type=float, default=0.9,
                    help="digital backoff (0.0-1.0)")
    ap.add_argument("--text", default=None,
                    help="payload to send (ASCII, up to 64 bytes). Default: callsign+seq")
    ap.add_argument("--data-rate", type=int, default=config.DATA_RATE,
                    help="symbol rate bps (default from config)")
    ap.add_argument("--deviation", type=int, default=config.FREQ_DEVIATION,
                    help="peak deviation Hz (default from config)")
    ap.add_argument("--freq", type=int, default=config.CENTER_FREQ_HZ,
                    help="carrier frequency Hz (default from config)")
    args = ap.parse_args()

    global SAMPLE_RATE, SAMPLES_PER_SYMBOL, DATA_RATE, FREQ_DEVIATION, CENTER_FREQ_HZ
    SAMPLE_RATE = args.sample_rate
    DATA_RATE = args.data_rate
    FREQ_DEVIATION = args.deviation
    CENTER_FREQ_HZ = args.freq
    SAMPLES_PER_SYMBOL = SAMPLE_RATE / DATA_RATE

    comp = build_composite(args.packets, args.gap_ms, args.digital_gain, args.text)
    comp.tofile(args.out)
    print(f"Wrote {args.out}: {len(comp)} bytes "
          f"({len(comp) // 2 / SAMPLE_RATE:.2f}s/loop @ {SAMPLE_RATE/1e3:.0f} kHz, "
          f"{CENTER_FREQ_HZ/1e6:.3f} MHz, {DATA_RATE/1e3:.0f} kbps, "
          f"+/-{FREQ_DEVIATION/1e3:.0f} kHz)")


if __name__ == "__main__":
    main()
