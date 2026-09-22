# AGENTS.md — GFSK Transceiver (contributor guide)

This file is for **contributors** working on the web app code. It documents the
hard rules that keep the DSP chain stable and the repo tidy. Bench-specific
session notes live in the private/internal repository; this public copy stays
generic.

## Project shape

The web app is one Flask process (`gfsk_ui.py`) that shells out to two
standalone Python DSP modules. There is no build step; dependencies are
`flask`, `waitress`, `numpy`, `scipy` (see `requirements.txt`).

```
gfsk_ui.py            Web UI + JSON API; creates SDR sub-processes
generate_gfsk_iq.py   GFSK modulator   -> int8 IQ file
decode_sst01.py       IQ file          -> decoded packets (burst + CRC)
decode_gfsk.py        legacy demodulator (superseded by decode_sst01.py)
config.py             RF parameters, presets, gain ceilings
nucleo_firmware/      optional Si443x-class/Nucleo transmitter sketches (.ino)
tests/                offline regression tests
```

## Run / test

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 gfsk_ui.py            # -> http://localhost:5000
python3 tests/test_roundtrip.py   # offline DSP regression gate (no radio)
```

`tests/test_roundtrip.py` must pass after any change to the modulator,
demodulator, CRC, or packet framing. It is the acceptance gate for the DSP.

## On-air packet format (do not change lightly)

```
[128 x 0xAA preamble][sync 0x2DD4][payload 1-32 bytes][CRC-16 CCITT 2 bytes]
```

- No length byte on air — the frame length is fixed and carried out-of-band
  (the app uses the length of the message it transmitted).
- Bits are MSB-first, not inverted.
- CRC-16 CCITT over the payload only: poly `0x1021`, init `0x0000`, emitted
  big-endian.
- Changing any of this requires updating generator + decoder + the round-trip
  test in the same commit.

## DSP conventions (rules, not suggestions)

1. **Fixed-length decoding only.** The decoder validates a specific payload
   length passed via `payload_len`. Passing no length (`None`) makes it
   auto-scan lengths 1..32 — never do that on a live/noisy capture; it is
   pathologically slow (and was the source of "decode hangs" bugs).
2. **Mean-subtract IQ** (remove DC) before burst search — a DC offset yields
   0 packets.
3. **Per-burst carrier lock.** Bursts can be sparse; do not rely on a single
   global carrier estimate.
4. **RX tuning convention.** The receiver tunes to `RX freq - 100 kHz` so that
   a peer on the RX-box frequency appears at **+100 kHz**, out of the DC bin.
   Keep TX/RX tuning and the decoder's frequency expectation consistent with
   this everywhere.
5. **Samples-per-symbol must be an integer.** Bench: 9600 bit/s @ 2.4 Msps =
   125 sps. Sat: 200 kbit/s @ 2.4 Msps = 12 sps. Fractions break demodulation.
6. **HackRF TX power is `-x` (0-47 dB), not `-g`.** `-g` is receive-only.
   For HackRF-to-HackRF links use `-x >= 30` with the amplifier on; back off
   only if the receiver saturates.
7. **Gain discipline.** Receiver clipping (OVERLOAD) looks like "0 packets",
   so start moderate (LNA 24 / VGA 48) and lower gain for strong/adjacent
   transmitters.

## Hardware (Si443x-class / Nucleo beacon — optional)

The companion transmitter is a Si443x-class radio driven from an STM32
Nucleo by bit-banged SPI, 434 MHz, fixed-length 7-byte frames (same format as
above). Firmware lives in `nucleo_firmware/`.

- Power the radio at **3.3 V**, never 5 V.
- Both SDN pins must be tied together and held low (active-high shutdown).
- Bit-bang SPI Mode 0 (CPOL=0, CPHA=0) at =< 1 MHz SCLK.
- Keep TXON and RXON both high during register access; drive the correct
  single line low only to actually transmit/receive.
- The decoder reads it like any other packet: `--payload-len 7`.

## Coding style

- Python 3.10+, vanilla stdlib where possible (no framework magic beyond Flask).
- Follow the style of the files you edit: 4-space indent, single quotes, no
  type annotations unless a surrounding function uses them.
- Keep the DSP functions pure and deterministic (numpy in-real-dtype out);
  app state stays in `gfsk_ui.py`.
- Don't comment what the code obviously does — comment *why*, and only when it
  is not visible from the code itself.
- Payload ceiling: TX accepts up to 64 bytes; the decoder's validated frame
  length ceiling is 32 bytes. Keep advertised supported message length =< 32.

## Do not

- No-op the DC subtraction (`iq - iq.mean()`) or the burst finder.
- Add a length byte to the on-air frame unless it is done in the generator AND
  decoder AND roundtrip test together.
- Let a capture path fall into the 32-length auto-scan on real radio data
  (use strict `payload_len`).
- Commit runtime artifacts: `*.iq`/`*.bin`/`*.wav`, `data/`, `logs/`,
  `ui.log`, `__pycache__/` are git-ignored — keep it that way.

## Contribution flow

- Fork the repo, branch, make focused commits (imperative messages), push, open
  a PR. Keep the DSP round-trip test green in the CI-equivalent step
  (`python3 tests/test_roundtrip.py`).
- When the on-air binary format changes, the commit must state so explicitly so
  peer radios are updated in lock-step.