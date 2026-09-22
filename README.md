# GALAMAD GFSK Transceiver

Ground segment web UI for GALAMAD Aerospace UHF link. Transmits GFSK via HackRF One, receives/decodes via RTL-SDR or HackRF.

## Requirements

- Python 3.10+
- HackRF One (for TX, and optionally RX)
- RTL-SDR (for RX, or use a second HackRF)

## Setup

```bash
# install dependencies
pip install -r requirements.txt

# install SDR tools (Ubuntu/Debian)
sudo apt install hackrf rtl-sdr

# verify devices
hackrf_info
rtl_test -t
```

## Run

```bash
# start the app
python3 gfsk_ui.py

# open in browser
http://localhost:5000
```

## Stop

```bash
pkill -f gfsk_ui.py
```

## Hardware Setup

- **TX**: HackRF One connected via USB
- **RX**: RTL-SDR or second HackRF connected via USB
- The app auto-detects connected devices on startup

## Presets

| Preset | Data Rate | Frequency | Use Case |
|--------|-----------|-----------|----------|
| bench  | 9.6 kbps  | 437.08 MHz | Lab/bench testing |
| sat    | 200 kbps  | 437.0 MHz  | Satellite PHY |

## Files

- `gfsk_ui.py` — Main app (Flask web UI)
- `config.py` — RF parameters and presets
- `decode_gfsk.py` — GFSK demodulator/decoder
- `generate_gfsk_iq.py` — GFSK IQ modulator
- `tests/test_roundtrip.py` — Offline roundtrip test
