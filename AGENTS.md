# AGENTS.md — EXA SST01 + Nucleo G474RE SPI Bring-Up

Prevent repeat of the 4-hour debug session (2026-08-28). Follow this exactly.

## 0) Canonical Layout (2026-09-05 — consolidated to ONE record)

`/home/luwangac/Downloads/PROSat Designs/SDR Apps/` is the canonical satellite
project folder. The git repo is the single record:

- **`SDR Apps/Communications/`** — the git repo (remote
  `https://github.com/GALAMAD-AEROSPACE/Communications.git`, branch `main`).
  The app lives at `Communications/Software/UHF/GFSK-Transceiver/` (gfsk_ui.py,
  decode_sst01.py, config.py, nucleo_firmware/, tests/, start.sh/stop.sh,
  AGENTS.md copy). Tag **`v0.1`** = known-good tested state. Commit here, then
  `git push origin main` (and tags). Recent-history restore: `git checkout v0.1`.
- **`SDR Apps/GALAMAD-GFSK-Transceiver/`** — SYMLINK → the repo subtree above.
  All the usual paths (`cd` here, `./start.sh`, edits) keep working; changes
  land directly in the git record. App runs from here; `:5000` cwd resolves to
  the repo. Do NOT put a real folder here.
- **`SDR Apps/SST01-Fixlen-TX/`** — independent project, OWN local git repo
  (tag `v0.1`), deliberately NOT pushed to GitHub. Firmware source of truth
  (sst_rxtx2 alternator, sst_rx, sst_rawtx, etc.) + README runbook.
- `SDR Apps/archive/2026-09-05-pre-consolidation/` — pre-consolidation
  snapshots of the superseded `GALAMAD-GFSK-Transceiver/` + `GALAMAD-GFSK-Clean/`
  folders. Read-only junk; do NOT edit. Older junk lives in `archive/`.
- `SDR Apps/*.iq`, `SDR Apps/data/`, `SDR Apps/logs/` — RF captures and runtime
  artifacts (data, not source; NOT committed).
- `SDR Apps/EXA_UHF_Satellite_Transceiver/` — separate project, leave alone.

Run the app (HOW to start :5000):
```
cd "/home/luwangac/Downloads/PROSat Designs/SDR Apps/GALAMAD-GFSK-Transceiver"
./start.sh                # starts python3 gfsk_ui.py (setsid), prints PID
# open http://localhost:5000
./stop.sh                 # to stop (or stop then start = restart)
```
Always via the symlink path; NEVER run `start.sh`/`stop.sh` from inside the
repo or the server cwd/id drift. See §19 for verification + the pkill trap.

**Git identity** (all commits/tags): `GIT_AUTHOR_NAME=christophergalamad
GIT_AUTHOR_EMAIL=christopher.coder@galamad.space` + `GIT_COMMITTER_*` (same).
Auth: PAT in `~/.git-credentials` (mode 600, `credential.helper store`);
created for user christophergalamad, user may revoke.

## 1) Hardware Wiring (Nucleo G474RE Arduino header)

| Nucleo | STM32 | SST01 | Cable | Notes |
|--------|-------|-------|-------|-------|
| D10 | PA4/PB6* | nSEL (CS) | Yellow | Active-low chip select |
| D11 | PA7 | SCLK | Blue | Host → SST01, Max 10 MHz Mode 0 |
| D12 | PA6 | SDO (MISO) | Blue | SST01 → Host, pullup when nSEL=H per ICD Fig.4 |
| D13 | PA5 | SDI (MOSI) | Blue | Host → SST01 |
| D9 | PA8 | SDN (black) | Black | Shutdown, active-high |
| D8 | PA? | SDN (clear) | Clear | **Second SDN — must be tied together with D9** |
| D3 | PB3 | RXON | Grey | RX mode: RXON=0 TXON=1 |
| D4 | PB5 | TXON | Orange | TX mode: TXON=0 RXON=1, **Idle: both H** |
| D2 | PA10 | nIRQ | Yellow | Input, pullup, H=idle |
| GND | — | DGND | Clear | Digital ground |
| 3.3V | — | VCC | Red | **Use 3.3V, not 5V** |

*Verify `D10` maps to `nSEL` on your board variant; `NUCLEO_G474RE` Arduino D10 = `PB6` in this build. Use `D10` as defined in firmware (`CS_PIN`).

**Critical gotchas:**
- **Two SDN pins**: PicoBlade has black SDN + clear SDN. Both must be LOW together (`D9` + `D8` driven LOW). If either >0.5V, chip stays in SHUTDOWN per ICD §8, registers lost, SPI returns `0xFF` (SDO pullup).
- **Three identical blue wires**: `SDO/SDI/SCK` look the same. Correct is `D11=SCK`, `D13=SDI`, `D12=SDO` (`Perm 1` in brute-force 2026-09-02; `Perm 0` `D13=SCK/D11=SDI` was wrong and returns `all 0x00`). Verified: `Perm 1` gives `0x00=0x08 0x01=0x06` (`/tmp/sst_brute/sst_brute.ino:39`). Other perms show `SDO` follows `SCK` (H on rise, L on fall) or `all 0x00`.
- **Two identical yellow wires**: `nSEL` vs `nIRQ` — verify with 1Hz toggle test (`nSEL` should follow `D10` 0↔3.3V at connector).

## 2) Power

- **VCC = 3.3V** (not 5V). ICD Table 6: `VIH=0.7*VDD`. At 5V `VIH=3.5V` > GPIO 3.3V → marginal SDI/SCLK. At 3.3V `VIH=2.31V` clean.
- DGND must be Nucleo GND. Both SDN pins 0V after POR. Measure at connector.

## 3) SDN / POR

- `SDN=H` = SHUTDOWN, no SPI. `SDN=L` = active. POR initiated on falling edge of SDN, 16 ms per ICD, we wait 300 ms (`delay(300)`).
- Drive **both** SDN wires LOW together. Firmware must `pinMode(D9+D8, OUTPUT)` and toggle both `HIGH→300ms→LOW→300ms`.

## 4) TXON / RXON

- Idle (SPI register access): `TXON=H RXON=H` (**this is the fix** — `RXON=L TXON=H` RX mode blocked reads, returned `all 0xFF`).
- TX: `TXON=0 RXON=1`
- RX: `TXON=1 RXON=0`

## 5) SPI (ICD §3)

- 3-wire + SDO, 16-bit transaction: `[R/W|A6..A0|D7..D0]`, MSB first, latched every 8 SCLK.
- `R/W=1` write, `R/W=0` read. Data clocked in on **positive** edge, out on **negative** edge → **Mode 0** `CPOL=0 CPHA=0` (`SPI_POLARITY_LOW`, `SPI_PHASE_1EDGE` in `UHF-SPI-Registers.ioc:177-178`).
- Bit-bang at **10 kHz** (50 µs delays) proven, 1 MHz (5 µs) also works. Prescaler 2 → 21 MHz on F401 exceeds 10 MHz max — avoid (`UHF-SPI-Registers/CMakeFiles`).
- Use bit-bang (`D11/D12/D13`) not hardware `SPI1` `PA5/PA6/PA7` on same header to avoid `NSS` conflict (`PA4`/`PB6`).

## 6) Verification Checklist (run in order)

1. **Continuity + toggle**: 1Hz toggle each `D10/D11/D13/D12` and measure at PicoBlade with multimeter 0↔3.3V. `SCLK` at SST01 should show ~1.6V average when toggling.
2. **SDN**: both pins 0V, DGND 0V.
3. **SPI scan**: read `0x00-0x7F`. Expect `0x00=0x08 0x01=0x06` (Si443x family), not `all 0xFF`. Our post-fix dump: `0x06=0x03 0x07=0x01 0x30=0x8D 0x6D=0x18 0x75=0x75 0x77=0x80`.
4. If `all 0xFF`:
   - Check both SDN LOW?
   - `TXON=H RXON=H`?
   - VCC 3.3V?
   - `Perm 0` (`SDO=D12` isolated H/H when `CS=H`)?

## 7) Firmware Reference

- Canonical test: `nucleo_firmware/sst01_test/sst01_test.ino` — bit-bang `bb_xfer` Mode 0, `CS_PIN=10 SCK=11 MOSI=13 MISO=12 SDN=9+8 RXON=3 TXON=4 IRQ=2` (`SCK=11/MOSI=13` per brute-force Perm 1 2026-09-02).
- Former engineer skeleton: `Software/UHF/UHF-SPI-Registers/Core/Src/main.c:69` empty, `SPI2` `PB10/PC2/PC3` at 21 MHz — not usable.

## 8) What Not To Do

- Don't power from 5V for SPI (logic mismatch).
- Don't drive only one SDN pin.
- Don't use `RXON=L` for register access.
- Don't use `SPI_BAUDRATEPRESCALER_2` (too fast).

## 9) WORKING End-to-End TX + "GALAMAD" DECODE (2026-09-02, verified)

The full SST01→HackRF→"GALAMAD" decode is SOLVED. Do not re-derive.

**Root cause of the 4-hr "can't decode FIFO packets" bug:** register `0x33`
(Header Control 2, D3=`fixpklen`) was 0x02 (variable length). In variable
mode the packet handler reads the packet length from FIFO[0] AND transmits
it, so the payload byte 0x47('G') became the length -> malformed packet.
The fix is FIXED packet length: `0x33=0x0A` (0x08 fixpklen | 0x02 2-byte sync).

**Firmware = `sst_fixlen.ino`** (project copy:
`nucleo_firmware/sst_fixlen/sst_fixlen.ino`; also `/tmp/sst_fixlen/sst_fixlen.ino`),
a copy of
`/tmp/sst_tx/sst_tx.ino` (0x30=0xAC PH+FIFO mode, 434MHz, 9600bps, GFSK BT=0.5,
dev ~5k) with ONLY these changes:
  - `{0x33,0x0A}`   (was 0x02)           <- THE FIX (fixed packet length)
  - loop `delay(30)` instead of `delay(1500)` (frequent bursts for capture)
  - ipksent wait `t0<150` instead of `t0<800`
  - FIFO loaded with ONLY `{'G','A','L','A','M','A','D'}` (NO length byte)
  - `0x3E=0x07` = fixed packet length = 7 data bytes

**On-air packet (verified by bit-level dump):**
  `[0xAA preamble*32][0x2D 0xD4 sync]['GALAMAD' 7B][0x00][0x32 CRC]`
- NO length byte on air (fixed-length mode); data MSB-first, no inversion
- CRC-16 CCITT (poly 0x1021, init 0x0000) over PAYLOAD ONLY (`crcdonly`=1,
  0x30 D5=1) = 0x0032, sent big-endian as `[0x00][0x32]`
- Bursts ~14ms, carrier at **+96.8kHz** from 433.9MHz tune (=433.997MHz)

**Decoder = `decode_sst01.py`** (same dir). Handles fixed-length no-length-byte
format, per-burst carrier lock (robust to sparse bursts):
  `python3 decode_sst01.py cap.iq --fs 2400000 --payload-len 7`
  Capture: `hackrf_transfer -r cap.iq -f 433900000 -s 2400000 -n 7200000 -a 0 -l 16 -g 32`
  Result: `text="GALAMAD"  <<< CONFIRMED` (thousands of CRC-valid packets).

**Gotchas learned:**
- `decode_gfsk.py` `carrier_offset` is unreliable for SPARSE bursts (searches
  only the first 27ms; mixed to wrong freq, 0 packets). Per-burst carrier lock
  (or tune to exact carrier 433.997MHz) avoids this.
- Burst detection needs mean-subtracted power; median*5 threshold on 1ms bins.
- In variable length mode (0x33=0x02): `[len][len][data]` if you ALSO prepend
  a length byte; and `[FIFO[0]]['rest']` if you don't (G=0x47 -> len 71). Never
  use variable length for an exact fixed payload -> use fixpklen=1.
- `0x30=0x21`/`0x71=0x2B` (spreadsheet "FIFO MODE") DOES NOT produce RF in this
  setup; only PH+FIFO `0x30=0xAC` transmits. Don't switch to manual FIFO mode.
- `{0x6D,0x1F}` (vs 0x18) appeared to kill RF in one test; keep `0x6D=0x18`.

**Reference firmware lineage (all in /tmp):**
  sst_tx.ino = transmits but malformed (var-len, G=len) -> strong RF, no decode
  sst_tx_len*.ino = prepended length byte -> double 0x07, still no clean decode
  sst_manual.ino = 0x30=0x21 FIFO mode -> NO RF
  sst_fixlen.ino = 0x33=0x0A -> CLEAN 'GALAMAD' decode  (CURRENT WINNER)
  sst_cw.ino / sst_pn9.ino = CW / PN9 continuous (good for RF/PA verification)

## 10) What Not To Do (additions)

- Don't use variable packet length (0x33 fixpklen=0) for exact fixed payloads.
- Don't trust `decode_gfsk.py` to auto-find carrier on sparse bursts.
- Don't switch to manual FIFO mode (0x30=0x21) — no RF in this setup.

## 11) Session 2026-09-04 — SST01 TX proven end-to-end; pending RX task

Independent project (new folder, per user directive; do NOT pollute the clean
GitHub base): **`SDR Apps/SST01-Fixlen-TX/`** — `receive.sh`, `decode_sst01.py`,
`firmware/{sst_fixlen,sst_vary,sst_cont}.ino`, `config.py`, `README.md`.

**Success (on air, CRC-valid):**
- `receive.sh` → `decode_sst01.py` decodes the fixed-length beacon reliably:
  `text="GALAMAD" CONFIRMED` (n~1100+).
- **`firmware/sst_vary/sst_vary.ino` = current winner flashed**: same POR cycle
  as `sst_fixlen.ino` but payload `GALA001, GALA002, ...` (7 chars, wraps at 999).
  Decodes 4 distinct CRC-valid messages per capture.

**Do NOT re-derive (pitfalls solved 2026-09-04):**
1. **RX gain clipping is the #1 invisible blocker.** SST01 carrier is very strong;
   high gain (`-l 16 -g 32`+) saturates the HackRF → flat/noise, "0 packets", "no RF".
   Always capture at **`-l 0 -g 8`**. (gqrx: set LNA/VGA to 0/8 to clear OVERLOAD.)
2. **`decode_sst01.py` CLI must mean-subtract** before `_scan` (DC offset → 0 packets).
   `decode()` does; `main()` now does too.
3. **Beacon period ≈ 781 ms** (13 ms burst; measured 781 ms × 8). ~1.3 bursts/s.
4. **gqrx keeps the RX HackRF busy** ("Resource busy", corrupted captures / bogus
   −666 kHz peak). Close AND kill the gqrx process before capturing — GUI-close
   alone leaves the process alive.
5. **Continuous ≠ variable.** ICD "auto-retransmit" (§ line 2126) = SAME packet,
   NO FIFO reload → gap-free. Varying needs FIFO reload each packet → needs the POR
   re-arm (→ ~781 ms rhythm). `sst_cont.ino` (reload, no POR) prints "TX n" but
   **radiates nothing**; don't attempt that path.

**PENDING NEXT TASK (user, 2026-09-04):** make the SST01 **both TX and RX**
(alternating, not simultaneous) on the Nucleo — add an RX path (RXON toggle,
FIFO readback, `ipkvalid` poll) to the proven TX cycle, same 9600 bps / 434 MHz /
fixed-len PHY. Start from `sst_vary.ino`'s proven register block.

## 12) Session 2026-09-05 — SST01 RX SOLVED; alternating TX+RX in work

**The RX blocker is CRACKED.** The SST01 NOW receives and demodulates its own
GFSK packets (fixed-len, sync 0x2DD4, CRC-valid). Root cause of "preaval never
fires": the POR-default clock-recovery block is tuned for a different data rate;
RX needs the **Si4432 9.6 kbps reference clock-recovery/IF/AFC register block**.

**RX-critical register block (the fix — was default before):**
```
{0x1C,0x12},   // IF filter BW ~102 kHz (POR 0x01 too narrow)
{0x1D,0x40},   // AFC loop gearshift override
{0x1E,0x0A},   // AFC timing control
{0x20,0xD0},   // Clock Recovery Oversampling Ratio rxosr=208 (POR 0x64 wrong)
{0x21,0x00},   // Clock Recovery Offset 2
{0x22,0x9D},   // ncoff=0x99D49 (POR 0x47AE wrong)
{0x23,0x49},
{0x24,0x00},   // Clock Recovery Timing Loop Gain
{0x25,0x24},
{0x2A,0x20},   // AFC limiter
{0x69,0x00},   // AGC override ON
```
Source: WildLab Si4432 9.6kbps reference (deviation 45kHz, filter 102kHz; we
keep our 0x72=0x08 ~5kHz dev). Do NOT revert to POR defaults for 0x1C/0x20-0x25.

**Proven RX entry (mirror in any RX firmware):**
```
wr(0x07,0x01); delay(80);
RXON=LOW TXON=HIGH; delay(80);
wr(0x08,0x02); delay(5); wr(0x08,0x00); delay(5);  // clear RX FIFO
rd(0x03); rd(0x04);
wr(0x07,0x05); delay(300);   // pllon|rxon  (0x05 REQUIRED; 0x11 no-pllon gives RSSI=0)
rd(0x03); rd(0x04); delay(50); rd(0x03); rd(0x04);
```
- `0x07=0x05` (pllon|rxon) is REQUIRED for RXI; `0x07=0x11` (rxon|xton, no
  pllon) → RSSI reads 0 (not in RX). This was an earlier red herring.
- RX works even though the packet's `0x4B` received-length reads **0xFF** in
  fixed-len mode; read the fixed 7 payload bytes from FIFO (0x7F) directly when
  ipkvalid (0x03 & 0x02) fires.
- Decodes the replayed GALA### sequence from `captures/cap.iq` reliably:
  `PAYLOAD: GALA227/228/229/230/233/234...` confirmed exact payload bytes.
- TX still works using the SAME shared register block (GALA001..003 CRC-confirmed
  on air with `sst_rxtx.ino`).

**Files:** `SST01-Fixlen-TX/firmware/sst_rx/sst_rx.ino` = proven standalone RX
decoder. `SST01-Fixlen-TX/firmware/sst_rxtx/sst_rxtx.ino` = alternating TX+RX
(needs final RX-in-cycle verification once HackRF re-plugged; added {0x69,0x00}
+ exact standalone RX-entry timing).

**Known subtlety:** in the alternating firmware the fixed 4s RX window after
each POR-rearm+TX did NOT log a decode in one trial (timing/settle); the
standalone RX path with the same block WORKS. Verify the alternating RX after
re-linking the HackRF.

## 13) Session 2026-09-05 (LSB) — RECORDED FACT + FIFO-TX regression findings

**RECORDED FACT (user, 2026-09-05): TX+RX DEFINITELY WORKED on 2026-09-04 on
this EXACT hardware, no hardware has changed since.** Do NOT re-derive; the
09-04 proven register block is unchanged (sst_fixlen.ino == GALAMAD repo +
archive copies; sst_vary.ino == 09-04 winner flashed). The 09-05 morning RX
"regression" was NOT firmware: `captures/cap.iq` today has a SINGLE burst, and
the 6s `-R` replay starved the modem. Replay `/tmp/dense.iq` instead (34 ms
burst slice tiled → decodes GALA152 × ~14000, locks RX instantly).

**09-05 LSB diagnostics (all with HackRF ...f52a7 @ `-l 0 -g 8`, antenna is
RX-adjacent):**
  - sst_cw (0x71=0x00 unmodulated, stays TX): RADIATES strong carrier
    (~+203 kHz; proves PA+RF switch+antenna fine). First TX entry after POR
    works; **subsequent re-entries to TX (even CW) are SILENT**.
  - sst_fixlen/sst_vary/sst_vary_1f/sst_rxtx/sst_diag (0x71=0x23 GFSK+FIFO,
    0x30=0xAC packet handler ON): report `ipksent st=0x24`, D0x07=0x9, but
    emit ZERO RF (swept 430.3–437.7 MHz). Single-shot FIFO right after fresh
    POR also emits nothing.
  - **BREAKTHROUGH bisect: 0x30=0x00 (packet handler OFF) + GFSK FIFO + raw
    32×0x55 fill → RADIATES sustained carrier (~770 ms, 448k× noise at
    −202.5 kHz).** The modulator/PA/FIFO path works TODAY; the AUTO PACKET
    HANDLER (enpactx=1 + packet regs) is what fails to key up TX. This is the
    #1 lead.

**Shorthand 0x07 bits (ICD): txon=D3 rxon=D2 pllon=D1 xton=D0** → `0x07=0x09`
= txon|xton (TX), `0x07=0x05` = rxon|xton (RX; earlier note wrongly said
pllon|rxon), `0x07=0x03` = pllon|xton (TUNE), `0x07=0x11` = x32ksel|xton
(READY — explains old "no-pllon RSSI=0" red herring). Regs: 0x6E/0x6F = TX-
data-rate 0x4EA4 (≈9.6k, proven); 0x6D D3=lna_sw MUST=1 (direct tie); 0x30
D7=enpacrx D3=enpactx; 0x70 D4=enphpwdn D0=enwhite.

**PENDING (do not re-derive; just bisect):** which packet-handler element
(0x30=0xAC alone vs +sync regs 0x35/0x36/0x37 vs 0x33/0x32/0x3E) blocks TX
key-up; then restore packet-mode TX, re-run sst_rxtx, confirm RX-in-cycle,
and update §12 (sparse `-R` replay gotcha already noted here).

## 14) Session 2026-09-05 (afternoon) — TX resolved via raw-FIFO; then RX-in-cycle
     CONFIRMED once replay used the right TX gain flag (`-x 47`, not `-g`)

**CANONICAL RUNBOOK: `SST01-Fixlen-TX/README.md` — read it before any bench work.
It has the full how-we-got-here trail, the exact working TX/RX/alternation recipes,
the `-x` vs `-g` rule, verification commands, and the "if it looks broken" checklist.**

**TL;DR: working TX code AND working RX code AND the alternating TX+RX loop are
ALL verified on air.**
- **TX code = `SST01-Fixlen-TX/firmware/sst_rawtx/sst_rawtx.ino`** — proven
  repeatedly on air today: decode_sst01.py returned `text="GALAMAD"` n=58 and
  alternator bursts `GALA000`(n=225)/`GALA001`(n=227) → `<<< CONFIRMED`
  (captures /tmp/rawtx.iq, /tmp/rxtx2_iq, /tmp/golden2.iq).
- **RX code = `SST01-Fixlen-TX/firmware/sst_rx/sst_rx.ino`** — morning-proven
  standalone (decoded GALA152/227/228/229/230/233/234 on air). Its config +
  entry are mirrored verbatim into the alternator below.
- **Alternator = `SST01-Fixlen-TX/firmware/sst_rxtx2/sst_rxtx2.ino`** — TX on
  air CONFIRMED (GALA000/001 after each fresh upload); RX window = exact
  sst_rx block (`cfg_rx()` + `0x07=0x01 → RXON=L → FIFO clear → 0x07=0x05 →
  poll 3 s → read 7 x FIFO 0x7F on ipkvalid`). DELIVERABLE=DONE.

**Why the RAW path works and the packet handler doesn't (final, 2026-09-05):**
- The proven register block is UNCHANGED since 09-04. What changed is a
  **chip-level TX key-up lifetime bug**: the PA keys up ONLY the first ~2 TX
  entries after a power event. `sst_cw` works because it never leaves TX.
  Interrupting a TX with an interleaved RX phase (`RXON=L`, `0x07=0x05`)
  makes subsequent TX entries go silent EVEN with per-cycle SDN POR —
  but `sst_rawtx` (TX-only loop, no RX phase) keeps re-radiating burst after
  burst (n=58+). Workaround: **re-UPLOAD the firmware before each TX-on-air
  demo** (fresh first-cycle burst always decodes).
- Byte-order here is the KEY: BOTH raw and packet TX use the raw-FIFO path of
  the silicon modem; the packet handler (enpactx=1, 0x30=0xAC) additionally
  mis-opens the FIFO to a 0xFF-length TX (underflow → no RF). Keep
  `0x30=0x00` (RAW FIFO, packet built in MCU):
  `[32x0xAA][0x2D 0xD4][7B payload][CRC16-CCITT big-endian]`,
  burst-write to `0x80|0x7F`, then `TXON=L`, `wr(0x07,0x09)`, `delay(60)`,
  `wr(0x07,0x01)`, `TXON=H`.

**RX-in-cycle CONFIRMED (do NOT re-derive).** Root cause of the afternoon's
"RX blind" was NOT firmware or coupling: the replay was started with `-g 40`
— **`-g` is an RX-ONLY gain in hackrf_transfer; TX power is `-x gain_db`
(0-47 dB)** — so the HackRF TX radiated at ~0 dB (nothing to receive; RSSI
flat 92 in `sst_rx` standalone AND in-cycle). With the replay run as
`hackrf_transfer -t /tmp/dense.iq -f 433900000 -s 2400000 -a 1 -x 47 -R`:
- `sst_rx.ino` standalone: `**PKVALID rxplen=0xFF PAYLOAD: GALA152`, RSSI 126.
- `sst_rxtx2.ino` alternator: **`rxwin got=1 / PAYLOAD: GALA152` in EVERY RX
  window**, in the same loop that also does its TX bursts.

**Use in ALL future hackrf_transfer TX/replay tests:** `-a 1 -x 47` (start at
`-x 47`, back off to `-x 20` if RX saturates). Note: the RX-window entries
depend only on the FIFO/replay reaching the chip; TX bursts from the alternator
STILL only radiate for the first ~2 cycles after upload (chip key-up quirk,
see below) — RX windows keep working regardless.

## 15) Session 2026-09-05 (evening) — localhost:5000 UI fixed end-to-end + SST01
     card now infers real radio state

**Decode fixed (3 stacked causes, all live-verified):**
1. Default RX gain was `LNA 24/VGA 48` → HackRF OVERLOAD/saturation. Change
   `GALAMAD-GFSK-Transceiver/gfsk_ui.py:90` defaults to `rx_lna:0, rx_vga:8`.
2. `:5000` was a stale Sep-4 process from the CLEAN repo
   (`python3 src/gfsk_ui.py`, cwd `GALAMAD-GFSK-Clean`). Kill it; ALWAYS launch
   from the canonical app dir via `./start.sh` (stop: `./stop.sh`). Note:
   `pkill -f 'gfsk_ui.py'` must NOT appear in a command that also contains the
   literal `gfsk_ui.py` or it kills its own shell — use `stop.sh`.
3. App auto-picked the BROKEN HackRF `...285067dc2a2a864b` (a864b, fw v2.0.1);
   `main()` now prefers any serial ≠ a864b (picks f52a7). ALSO the app's
   `decode_sst01.py` differed from the working `SST01-Fixlen-TX/` copy and its
   `decode()` scanned ALL payload lengths → bogus CRC-collision junk. Actions:
   synced decoder in, added `payload_len` kwarg, `decode_capture()` now passes
   `payload_len=dec.DEFAULT_PAYLOAD_LEN` (== CLI `fixed_lens=[7]`).
   Verified: `POST /receive` → `{"packets":[{text:"GALA326", crc_ok:true,
   occurrences:227, len:7}]}`.

**SST01 card no longer lies about state (was: blinking icon + "idle" while the
alternator radiated).** Root cause: pill only mirrored the app's commanded
`_sst["mode"]`, but autonomous firmware (sst_rxtx2) ignores app commands. Now:
- Server `_sst_drain()` (under `_sst["lock"]`, non-blocking) parses real serial
  into `auto`(True=autonomous fw/False=command fw) + `activity`('tx'|'rx',
  fresh ≤8 s): `PAYLOAD:`/`RX[`/`ipkvalid`→rx, `rxwin got=`→rx, `GALA%03d`→tx,
  `TX[`/`ipksent`/`TX done`/`PONG`/`READY.`→command-fw tx. `rx_msgs` fills from
  PAYLOAD lines.
- `/sst01/status` returns `auto`/`activity`; when auto, forces `mode=null` and
  **`/sst01/mode` + `/sst01/send` refuse with 400** ("radio is running its
  autonomous TX/RX firmware — commands need sst_rx_tx.ino"). Buttons only work
  with the command firmware flashed.
- UI pill: presence dot is now SOLID (no blink metaphor); pill shows the THREE
  main radio states = `AUTO RX/TX` (autonomous alternator — sst_rxtx2 turns
  both ON, detected via its `rxwin got=` marker) | `RX` | `TX` (command
  firmware commanded, or autonomous beacon/`RX`-only evidence), plus the
  transitional `idle` (commanded) / `connected` / `offline`. Pill highlights
  (`.pill.act`) whenever a real state is live; hint line tells the user
  buttons need command firmware when `auto` is detected.

**Verified live against flashed sst_rxtx2 alternator (no replay):**
`{"state":"AUTO RX/TX","auto":true,"activity":"rx","mode":null,
"last_state":"rxwin got=0"}` polled continuously — pill reads "AUTO RX/TX" +
highlighted instead of "idle". Confirm payloads appear in `#sstlog` by running
the dense replay (`-a 1 -x 47`) while watching the card.

## 16) Session 2026-09-05 (LSB) — signal-panel fix + machine consolidation to ONE record

**UI "Signal Strength" panel lie fixed (`gfsk_ui.py _signal_metrics`).** Symptoms:
"OVERLOAD — reduce gain" + "No signal" while packets decoded fine (GALA205). Three
root causes, all verified live:
1. **Duplicate `"peak_dbfs"` key** in the return dict — the second (FFT power
   `10*log10(|fft|^2)` in raw array units) overrode the true dBFS peak, showing
   +21..25 "dBFS". Removed the duplicate; `peak_dbfs` is now the real `db(max|iq|)`.
2. **`saturating = peak > 0.98`** — int8 full-scale samples sit at 127/128=0.992,
   so ANY strong burst legitimately hit the clip and the panel screamed OVERLOAD.
   Now `peak > 0.98 AND rms_db > -12` (whole capture running hot), else it's just
   a normal strong burst. Verified: `/signal` peak 2.7 dBFS, no OVERLOAD.
3. **"No signal" from whole-capture RMS** is misleading for the sparse 781 ms
   beacon (14 ms bursts lower the mean). `/decode` now sets `metrics["decoded"]`
   and overrides `quality="Strong"` when CRC-valid packets exist. Verified: 5
   synthetic GALA002..006 bursts → `{"quality":"Strong","peak_freq_khz":96.8,
   "peak_dbfs":-8.0,"saturating":false}`, carrier +96.8 kHz nailed.

**Consolidation to ONE record (user directive).** The repo *is* the record now:
- `SDR Apps/GALAMAD-GFSK-Transceiver/` is a **symlink** → `Communications/
  Software/UHF/GFSK-Transceiver/` (the git subtree). Edits/`./start.sh`/`:5000`
  all resolve into the repo; nothing is duplicated on disk.
- Pre-consolidation folders moved to `archive/2026-09-05-pre-consolidation/`
  (read-only snapshots): the old `GALAMAD-GFSK-Transceiver/` working folder and
  the stale `GALAMAD-GFSK-Clean/`.
- `.gitignore` hardened: canonical working `.gitignore` (which lacked `*.iq`
  `*.bin` `*.wav` guards) was replaced by the repo's version — prevents capture
  pollution (`data/`, `logs/`, `*.iq` are ignored).
- **`pkill` self-match trap (AGENTS.md §15) bit us during this change:** a shell
  command line that literally contains `gfsk_ui.py` gets killed by `./stop.sh`'s
  `pkill -f gfsk_ui.py`. Never mix a literal `gfsk_ui.py` on the same command
  line as `stop.sh`; use globs (`g*fsk_ui.py`) when you must.

## 17) Session 2026-09-05 (LSB, after lunch) — Drive HackRF TX from the WEB UI; SST01 RX confirmed from it

**You do NOT need a terminal to TX from the HackRF — the app has it.** Proven
2026-09-05: `POST /send {"message":"GALA152"}` (the exact call the UI Send
button makes) started `hackrf_transfer -t <gen>.iq -f 434000000 … -R`, and the
flashed sst_rxtx2 alternator received it live:
`rx_msgs":["PAYLOAD: GALA152","PAYLOAD: GALA152"]` on `/sst01/status`.

**UI recipe (SOP, no terminal):**
1. Top **Transmit panel**: message box holds the text. Keep it **EXACTLY 7 chars**
   (SST01 RX is fixed-len 7 + sync 0x2DD4 + CRC16) — `GALA###` like the beacon.
2. Click **Send** → HackRF repeats the generated GFSK IQ (`-R` loop); TX dot=ON.
3. Watch the **SST01 card `#sstlog`** → `PAYLOAD: GALA###` within one ~4 s RX window.
4. **Stop** button halts the loop (`POST /stop`).

**Replay an arbitrary .iq from the UI:** the **file upload** → `/sendfile` → TX
it in a loop (this is `hackrf_transfer -t file -R` with no terminal). `dense.iq`
replay = the proven dense-tiled GALA152 test vector.

**Gotchas (learned live):**
- The TX frequency = the app **freq box** (default 434.000). SST01 crystal sits
  ~433.997; 3 kHz off is inside AFC/BW — locks. If a future day doesn't lock,
  set freq box to 433.997.
- TX gain: `tx_gain` (0-47) via `/txcfg`; default 30 works; use the new
  TX-power rule (`-x 47`, back off to 20 if RX saturates) — apply the same
  thinking to the UI `tx_gain`.
- **`tx_running` in `/status` is the truth meter**: if a process is holding the
  HackRF (e.g. gqrx), `/send` returns ok but no RF goes out.
- User directive: gqrx open/closed is the USER's responsibility on the bench —
  do NOT bake a `kill gqrx` into the SOP.

## 18) Session 2026-09-05 (LSB, afternoon) — SST01 TX visibility + HackRF fallback hardening

**1. UI now shows BOTH directions of the SST01.** The alternator (sst_rxtx2.ino)
only printed its RX (`PAYLOAD:`, `rxwin got=`); its own TX was silent. Now:
- Firmware prints `TX: GALA###` as the payload leaves the MCU (re-UPLOAD
  `SST01-Fixlen-TX/firmware/sst_rxtx2/sst_rxtx2.ino` to see it).
- `gfsk_ui.py _sst_ingest` parses `TX:` → `tx_msgs` (last 20); `/sst01/status`
  returns `tx_msgs`; the card `#sstlog` renders `SST TX: <line>` and
  `SST RX: <line>` so the user sees what the radio sends AND receives.

**2. GALA666 UI demo failed then got fixed — a NEW HackRF wedge.** `/send` said ok
but `tx_running` stayed False and hackrf_transfer went zombie in <0.3 s. The
wedged **a864b** now fails `hackrf_board_id_read() → I/O error` and bricks
`hackrf_info` enumeration → app `_list_hackrf()` returned `[]` → no `-d` →
hackrf_transfer opened device 0 (wedged a864b) → died. Direct
`hackrf_transfer -d <f52a7>` still radiated fine (proves f52a7 is good).
Fixes in gfsk_ui.py:
- `_list_hackrf()` keeps ONLY hex-serial lines (`[0-9A-Fa-f]{8,}`) so garbage
  reads can't become "devices".
- `start_tx` uses `_sel["hackrf_serial"] or _sel["hackrf_rx_serial"]` so TX
  inherits the good RX device instead of leaving `-d` off.
- `main()` falls back to known-good serial `000000000000000016bc62dc2e4f52a7`
  (f52a7) when enumeration is wedged but no RTL-SDR is present.
Verified live: with fallback active, `POST /send {"message":"GALA666"}` →
`tx_running True` → SST01 card `rx_msgs:['PAYLOAD: GALA666','PAYLOAD: GALA666']`,
`last_state rxwin got=1` (twice).

**Reported-flow now:** TRANSMIT box (message ≤7 chars e.g. GALA666) → Send →
HackRF radiates it → SST01 RX window → card shows `SST RX: PAYLOAD: GALA666`.
SST01's own TX appears as `SST TX: TX: GALA###` once the updated sketch is
reflashed. Bench note: physically unplugging the broken a864b would let
`hackrf_info` enumerate normally again; the app now self-heals regardless.

**3. Timestamps.** SST01 card lines now carry machine time: `[%H:%M:%S]` prefixes on every `PAYLOAD:`/`TX:` message and the `last_state` line (added `_ts()` at ingest; `rx_msgs`/`tx_msgs`/`last_state` only).

## 19) End of day 2026-09-05 — wrap-up (all verified live)

**Daily app start (:5000):**
```
cd "/home/luwangac/Downloads/PROSat Designs/SDR Apps/GALAMAD-GFSK-Transceiver"
./start.sh        # now: python3 gfsk_ui.py via setsid; open http://localhost:5000
./stop.sh         # to stop/restart. pkill trap: never put the literal .py filename
                  # on the same command line as stop.sh (it kills its own shell).
```
Verify: open http://localhost:5000 — operating once when alternator is flashed, the
SST01 card shows `AUTO RX/TX`; `curl :5000/sst01/status` → `state="AUTO RX/TX"`.

**Day's key highlights (recorded in §16-§18):**
- UI "Signal Strength" panel fixed (3 causes): duplicate peak_dbfs key, false
  OVERLOAD on legit full-scale bursts, "No signal" on sparse beacon RMS.
- Machine consolidated to ONE record: `Communications/` git repo +
  `GALAMAD-GFSK-Transceiver/` symlink + `SST01-Fixlen-TX/` local repo.
- HackRF TX fully driveable from the web UI (TRANSMIT box ≤7 chars,
  + file-upload for raw .iq replay) — SST01 card shows the received payload.
- SST01's OWN TX now reported: updated `sst_rxtx2.ino` prints `TX: GALA###`;
  card shows `SST TX:` / `SST RX:` lines with machine `[HH:MM:SS]` timestamps.
  RE-UPLOAD `SST01-Fixlen-TX/firmware/sst_rxtx2/sst_rxtx2.ino` to the Nucleo to
  see the TX lines (and to re-arm the first-cycle TX burst).
- HackRF hardening: the broken a864b now wedges `hackrf_info` enumeration;
  app self-heals by falling back to known-good f52a7
  (`000000000000000016bc62dc2e4f52a7`) for RX and TX. Physically unplug the
  a864b whenever convenient.

**Resync points:** `Communications/` → committed & pushed, `main`=`e06b8e3`;
known-good tag `v0.1` (`cf49615`) intact; `SST01-Fixlen-TX` committed locally
(`6f5c491`), not pushed (by design).

## 20) Session 2026-09-22 — HackRF <-> HackRF GFSK link verified (no RTL)

**The two HackRFs now talk GFSK to each other** (f52a7 TX -> a864b RX decoded
`GALA152`, crc_ok, ~1748 occurrences, carrier +100.0 kHz). Hard facts:
- f52a7 (good): reliable TX **and** RX. a864b (2nd unit): now READS fine
  (fw 2023.01.1, same as f52a7; earlier "wedged/v2.0.1" notes are STALE).
- a864b as TX = SILENT — captures on f52a7 show flat noise (rms 2.3-2.5,
  no bursts), even with amp ON (-a 1 gain 40). Do NOT use a864b to transmit.
- a864b as RX WORKS but needs HIGH gain: LNA 24 / VGA 48 (LNA 0/VGA 8 ->
  carrier at +100 kHz but ~3 dB SNR, 0 packets). Do NOT apply the strong-
  beacon rule (lna0/g8) to it; that rule is for the SST01 carrier.
- App /receive path validated end-to-end; decode_sst01.py hangs (timeout)
  on some weak-noisy a864b captures but the app route returns fast (uses
  its own scan).
- RTL-SDR: user has one but it is NOT on the USB bus (lsusb shows only the
  two HackRFs); test was replanned to HackRF<->HackRF per user.

**Live app config now set for user bench testing:** TX device = f52a7
(hackrf_serial), RX device = a864b (hackrf_rx_serial), rx_lna 24, rx_vga 48,
tx_gain 40 amp ON, freq 434.000 MHz. Recipe: type a 7-char message in the
Transmit panel (e.g. GALA152), Send, then Receive, watch the packets panel.

## 21) Session 2026-09-22 — decode_sst01.py pathological-slowness FIX (minutes -> ~3s)

**Symptom:** /receive hung decoding for minutes while TX loop (f52a7, amp/gain40) sent
GALAMAD into a864b RX at LNA24/VGA48.

**Root cause (cProfile):** _scan correlated every phase against SYNC; the abs
threshold (0.15 x len(tpl)=2.4) was meaningless because a saturated/strong burst
makes the demodulated symbols LARGE (not ~±1), so ~ALL corr offsets passed and
bits_to_bytes+crc16 ran ~1.36M times per 2s capture (was 55 0.000000alse-positive rate).

**Fix (decode_sst01.py _scan):**
1. Normalize before correlating: `corr = np.correlate(sym/_scale, tpl)` where
   _scale = sym.std() (bit decimation downstream uses sign only, unaffected).
   Hit rate dropped ~100% -> 2.2%.
2. Candidate filter vectorized: `for j in np.where(abs(corr)>=thr)[0]` (was a
   pure-python loop over every corr bin).
3. bits built with numpy slice (was a python list comp).
4. find_bursts caps any run at MAX_BURST_MS=250ms (saturated capture merged the
   whole window into one "burst"; _demod_burst FIR+oaconvolve would chew
   14.4M samples).

**Verified live:** 6s a864b capture of GALAMAD decoded in 2.97s (was 60s+/
minutes), n=2633 crc_ok. /receive round-trip ~9s (6s capture + ~3s decode).
Command line decode of the SAME file that previously hung: text="GALAMAD"
CONFIRMED. UI at :5000 left running (PID 85660) with TX=f52a7, RX=a864b,
LNA24/VGA48, tx_gain40 amp, freq 434.000, message GALAMAD must be <=7 chars.

## 22) Session 2026-09-22 — app default TX freq -> 437 MHz; verified HackRF<->HackRF at 437

**Change:** gfsk_ui.py:53 _link default freq 434.000 -> 437.000 MHz (fresh / start.sh
comes up at 437.000, rx_tune 436.900 = carrier at +100 kHz). No SST01 code or
firmware touched (user directive). config.py CENTER_FREQ_HZ was already 437.0.

**"no packets" recurrence (user report):** NOT a decode regression — the app was
being used with the "bench" preset settings left at freq 437.000 / tx_gain 10.
At -x 10 the a864b RX sees the carrier (peak ~-13 dBfs) but cannot demod symbols
-> 0 packets, fast. Fix is tx_gain >= 30-47 (amp on). tx_gain 40 + amp ON verified.

**Verified live at 437 MHz:** /send GALAMAD (f52a7, amp, x40) -> a864b RX
LNA24/VGA48 -> packets [{text:"GALAMAD", crc_ok, occurrences:728, len:7}],
carrier +100.0 kHz, quality Strong, /receive round-trip 8.8 s. Rule: the
HackRF<->HackRF GFSK link is freq-INDEPENDENT (both radios move with the freq
box); only the SST01/Nucleo is quartz-locked ~433.997 MHz, so switch the freq
box to 434.000 when testing SST01 (app tuned to 437 will NOT hear the beacon).

## 23) Session 2026-09-22 — INDEPENDENT TX and RX frequency boxes

**Change (gfsk_ui.py):** _link now holds {freq, rx_freq}, both default 437.000 MHz.
- /freq accepts freq_mhz/freq_hz (TX) AND rx_freq_mhz/rx_freq_hz (RX), each
  optional; _parse_freq helper validates range.
- _rx_tune() = rx_freq - 100 kHz (proven +100 kHz carrier convention kept; DSP
  untouched). RX box is the frequency you intend to HEAR (peer TX carrier);
  tune is displayed too.
- /status, /spectrum, UI: two inputs (#freq TX, #rxbox RX); txfreq/rxfreq/rxtune
  status spans; applyFreq posts both.

**Why (user):** a receive-only computer wants to type 437 and literally receive
437 MHz; it must not be coupled to the TX box or the +100/(offsets). With
separate boxes the two ends can be slightly different by design.

**Verified:** defaults 437/437; rx-only POST leaves TX untouched; bad/absent
freq rejected; /freq returns {freq_hz, rx_freq_hz, rx_tune_hz}.

**Bench blocker (hardware, NOT the app):** the a864b RX unit
(0000000000000000285067dc2a2a864b) is NO LONGER on the USB bus. In its place a
DIFFERENT HackRF enumerates: 0000000000000000436c63dc388f4163. That new unit:
- as RX: only sees faint carrier at LNA24/VGA48 (peak -16 dBFS, 0 packets);
  saturates/clips at LNA40/VGA62 (rms -6, peak 3 dBFS) yet still 0 packets.
- as TX: radiates strongly but LO lands ~200 kHz LOW (told 437.000, carrier
  found at 436.8 -> set RX box to 436.8 to center it). This is an RX-DODGY
  unit like the old a864b class.
So live HackRF<->HackRF decode verification is blocked until a proven RX HackRF
(a864b re-plugged, or f52a7 as RX with a good TX on the other end) is attached.
f52a7 (000000000000000016bc62dc2e4f52a7) remains the reliable TX+RX unit.

**Committed 2026-09-22 (was a long-pre-existing local edit):** config.py
TX_GAIN_MAX 40 -> 47. Harmless; UI slider caps at 40 (gfsk_ui.py), but the
config ceiling now matches the HackRF max and the `-x 47` rule (AGENTS §14/§22).

**Follow-up UI refinements (same session):**
- RX frequency box moved OUT of the TRANSMIT card into the RECEIVE card (under the
  LNA/VGA controls), so all RX info lives with the receiver. TX box now shows only
  "sending on <freq>". RX box shows "listening to <rx_freq> / RX tune <tune>".
- Default RX gain restored to LNA 24 / VGA 48 (gfsk_ui.py _sel default; supersedes
  the lna0/g8 rule in §15, which applies only to the strong SST01 beacon). User
  directive: 24/48 is the proven best for HackRF<-\>HackRF RX.

**Subsequent (same day):** a864b is BACK on the USB bus (3 HackRFs now: f52a7,
a864b, 8f4163). "No decoding working" was NOT the app/layout — the RX selector
was still latched to the dodgy 8f4163. Fix: POST /select {'rx_kind':'hackrf',
'hackrf_rx_serial':'0000000000000000285067dc2a2a864b'} (LNA24/VGA48). Verified
GALAMAD crc_ok n=1510, carrier +100.0 kHz, Strong at freq 437 / rx_freq 437 (tune 436.9).

## 24) Session 2026-09-22 — waterfall removed; long-message decode FIXED (app)

**User request: remove the waterfall panel.** Done — canvas + wf JS gone from
gfsk_ui (UI only; the /spectrum endpoint and preempt logic are kept untouched).

**Decode slowness root cause (user: "decode taking soooo long" while sending a
25-byte message):** `decode_capture` always decoded at `payload_len=DEFAULT` (7),
and on 0 hits fell back to `decode_majority`, which called `decode(keep_bad=True)`
with `fixed_lens=None` → scanned ALL 32 payload lengths on a noisy capture.
Measured: **47 s then `TypeError('tuple indices...')` crash**. Strict decode at
the correct length is ~1-4 s.
**Fix (live-verified):**
- `gfsk_ui.decode_capture` now tries the CURRENT TX message length first
  (`len(_tx["msg"])`, so any 1..32-char app message decodes fast), then
  DEFAULT(7), then a CAPPED majority fallback — all in try/except. The app path
  never runs the 32-length auto-scan.
- `decode_majority(path, fs, lo_center=None, payload_len=None)` forwards the cap
  to `decode()` and is crash-proofed (try/except → []).
Verified on air (f52a7 TX x40+amp → a864b RX LNA24/VGA48, freq/rx_freq 437/437):
`/send "HELLO FROM GALAMAD AEROSPACE"` → `/decode` = `txt HELLO FROM GALAMAD
AEROSPACE` (~9 s wall capture+decode); 7-byte GALAMAD still decodes (regression ok).

**Length rules (unchanged):** decode_capture accepts any 1..32 len for the
HackRF<->HackRF link; the SST01 firmware/card remains fixed-7. TX accepts ≤64 B.

**Config after restart is NOT persisted:** server comes up with defaults (bench;
RX auto = first hackrf); re-apply via the two POSTs
`/select {'hackrf_serial':f52a7,'hackrf_rx_serial':a864b}` and
`/txcfg {'tx_gain':40,'amp':true}` (freq/rx_freq default 437/437).

**Tooling warning (hit again):** `stop.sh` ALSO pkills `hackrf_transfer` — never
put the literal string `hackrf_transfer` (or `gfsk_ui.py`) on the same shell
command line as stop.sh; the pkill kills your own shell too.
