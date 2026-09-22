#!/usr/bin/env python3
"""Offline regression test: generate GFSK IQ -> demod/decode -> recover text.

This exercises the full DSP chain (modulator + demodulator + CRC) with no RF
hardware, so it is deterministic and catches regressions the moment they
appear. The real HackRF->RTL-SDR link is a separate verification step only.

Run:  python tests/test_roundtrip.py
"""

import os
import sys
import tempfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import config
import generate_gfsk_iq as gen
import decode_gfsk as dec

TX_RATE = config.TX_SAMPLE_RATE   # 2.4 MHz -> 250 samples/symbol (integer)


def run_case(text: str) -> list:
    """Generate a single composite with `text`, decode it, return decoded texts."""
    gen.SAMPLE_RATE = TX_RATE
    gen.SAMPLES_PER_SYMBOL = TX_RATE / config.DATA_RATE
    comp = gen.build_composite(packets=5, gap_ms=50, digital_gain=0.9, text=text)
    with tempfile.NamedTemporaryFile(suffix=".iq", delete=False) as f:
        tmp = f.name
    comp.tofile(tmp)
    try:
        # generated composite is baseband at DC
        packets = dec.decode(tmp, TX_RATE, lo_center=0)
        return [p[0].decode("ascii", errors="replace") for p in packets]
    finally:
        os.remove(tmp)


def main():
    failures = 0

    for i, msg in enumerate([
        "HELLO FROM GALAMAD AEROSPACE",
        "TEST 12345",
        "HELLO WORLD",
    ]):
        got = run_case(msg)
        ok = msg in got
        print(f"  [{'+' if ok else '!'}] round-trip {i}: {msg!r} -> {got}")
        if not ok:
            failures += 1

    print(f"\n{0 if not failures else failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
