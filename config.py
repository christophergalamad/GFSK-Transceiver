"""Central configuration for the GALAMAD UHF GFSK Transceiver tool.

v2: retargeted to the GALAMAD satellite (SST01/SST02 = Si4463) PHY —
200 kbps, +/-50 kHz, 437 MHz, GFSK BT=0.5, sync 0x2DD4.

All RF / DSP knobs live here so experiments (rate, deviation, BT,
frequency) are one-line changes instead of edits scattered across scripts.
"""

# --- On-air format (Si4463 / SST01 compatible) --------------------------
CENTER_FREQ_HZ = 437_000_000   # 437.0 MHz (SST01 carrier; verify exact channel)
DATA_RATE = 200_000            # bps  (SST01: 200 kbps)
FREQ_DEVIATION = 50_000        # +/- Hz (SST01: +/-50 kHz) -> mod index 0.5
BT = 0.5                       # Gaussian BT product (SST01)
SYNC_WORD = 0x2DD4             # 16-bit sync word (2D D4)
PREAMBLE_BYTES = 128           # 0xAA preamble (extra preamble is fine for Si4463)
MAX_PACKET_LEN = 64            # max payload bytes

# --- TX (HackRF) --------------------------------------------------------
TX_SAMPLE_RATE = 2_400_000     # HackRF requires >= 2.0 MHz (12 samples/symbol)
TX_DIGITAL_GAIN = 0.5          # int8 amplitude backoff (0..1)
TX_ATTENUATION = 30            # hackrf_transfer -x (0..47 dB)
TX_GAIN_MAX = 47               # HackRF max; 40 was too low to decode (see AGENTS §14)
TX_AMPLIFIER = 0               # hackrf_transfer -a (1 = amp on, 0 = off)

# --- RX (RTL-SDR) -------------------------------------------------------
RX_SAMPLE_RATE = 2_400_000     # 12 samples/symbol at 200 kbps
RX_GAIN = 0                    # 0 = auto; RTL-SDR DC-offset saturates > 0
RX_TUNE_OFFSET = -100_000      # tune carrier-100k => GFSK at +100 kHz baseband
RX_LO_OFFSET = 100_000         # carrier expected at this IF in baseband
RX_CAPTURE_SECONDS = 6.0
RX_LOWPASS_CUTOFF = 150_000    # channel BW ~ 2*Fd + Rb = 300 kHz -> Fd + Rb/2

# --- App ----------------------------------------------------------------
DEFAULT_MESSAGE = "HELLO FROM GALAMAD AEROSPACE"
