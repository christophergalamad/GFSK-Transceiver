#!/usr/bin/env python3
"""Build DOPPLER_TEST_GUIDE.pdf — the GALAMAD GFSK Doppler lab test guide.

Regenerate after app changes:  python3 make_doppler_guide.py
Requires: reportlab (already a dev dependency of this box).
"""

import time

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                ListFlowable, ListItem, PageBreak, PageTemplate,
                                Paragraph, Preformatted, Spacer, Table,
                                TableStyle)

OUT = "DOPPLER_TEST_GUIDE.pdf"

GOLD = colors.HexColor("#c8890f")
GOLD2 = colors.HexColor("#f2b13a")
INK = colors.HexColor("#1a1410")
COCOA = colors.HexColor("#3a2c1c")
PAPER = colors.HexColor("#fbf7f0")
MUT = colors.HexColor("#6b5c4a")
GREEN = colors.HexColor("#2f7d4f")
LINE = colors.HexColor("#d8c9a8")

ss = getSampleStyleSheet()
S = {}
S["title"] = ParagraphStyle("title", parent=ss["Title"], fontName="Helvetica-Bold",
                            fontSize=25, leading=29, textColor=INK, spaceAfter=4)
S["subtitle"] = ParagraphStyle("subtitle", fontName="Helvetica", fontSize=12.5,
                               leading=17, textColor=MUT, alignment=TA_CENTER, spaceAfter=2)
S["brand"] = ParagraphStyle("brand", fontName="Helvetica-Bold", fontSize=11,
                            textColor=GOLD, alignment=TA_CENTER, spaceAfter=14)
S["h1"] = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=15.5, leading=19,
                         textColor=GOLD, spaceBefore=16, spaceAfter=6)
S["h2"] = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16,
                         textColor=COCOA, spaceBefore=11, spaceAfter=4)
S["body"] = ParagraphStyle("body", fontName="Helvetica", fontSize=10.3, leading=14.6,
                           textColor=INK, spaceAfter=6)
S["bullet"] = ParagraphStyle("bullet", parent=S["body"], spaceAfter=2)
S["note"] = ParagraphStyle("note", fontName="Helvetica-Oblique", fontSize=9.6, leading=13.2,
                           textColor=MUT, spaceAfter=6)
S["code"] = ParagraphStyle("code", parent=ss["Code"], fontName="Courier", fontSize=8.6,
                           leading=11.4, textColor=INK, backColor=colors.HexColor("#f0e9db"),
                           borderColor=LINE, borderWidth=0.5, borderPadding=6,
                           leftIndent=2, spaceBefore=2, spaceAfter=8)
S["cell"] = ParagraphStyle("cell", fontName="Helvetica", fontSize=9.2, leading=12.2, textColor=INK)
S["cellb"] = ParagraphStyle("cellb", parent=S["cell"], fontName="Helvetica-Bold")
S["th"] = ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=9.4, leading=12.4,
                         textColor=colors.white)
S["toc"] = ParagraphStyle("toc", fontName="Helvetica", fontSize=10.3, leading=16, textColor=INK)


def P(t):
    return Paragraph(t, S["body"])


def bullets(items):
    return ListFlowable(
        [ListItem(Paragraph(t, S["bullet"]), leftIndent=14, value="circle") for t in items],
        bulletType="bullet", start="circle", leftIndent=14, spaceBefore=2, spaceAfter=8)


def code(t):
    return Preformatted(t, S["code"])


def table(header, rows, widths):
    data = [[Paragraph(h, S["th"]) for h in header]] + \
           [[Paragraph(c, S["cell"]) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f1e6")]),
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def callout(title, text, tone=GOLD):
    inner = [[Paragraph("<b>%s</b>" % title, ParagraphStyle(
        "ct", parent=S["cell"], textColor=tone, fontName="Helvetica-Bold"))],
        [Paragraph(text, S["cell"])]]
    t = Table(inner, colWidths=[170 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PAPER),
        ("BOX", (0, 0), (-1, -1), 0.8, tone),
        ("LINEBEFORE", (0, 0), (0, -1), 3, tone),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


# --------------------------------------------------------------------------
content = []
A = content.append

# ---- cover -------------------------------------------------------------
A(Spacer(1, 26 * mm))
A(Paragraph("GALAMAD&nbsp;AEROSPACE", S["brand"]))
A(Paragraph("GFSK Transceiver", S["title"]))
A(Paragraph("Doppler Robustness &mdash; Lab Test Guide", S["subtitle"]))
A(Spacer(1, 3 * mm))
A(Paragraph("Testing TX/RX resilience to orbital Doppler from a stationary bench",
            S["subtitle"]))
A(Spacer(1, 10 * mm))
A(table(
    ["Field", "Value"],
    [["Document", "Doppler Robustness Lab Test Guide"],
     ["Applies to", "GALAMAD GFSK Transceiver web app (gfsk_ui.py) + doppler.py"],
     ["Link under test", "437.000 MHz, 200 kbps, &plusmn;50 kHz dev, GFSK BT=0.5, sync 0x2DD4"],
     ["Hardware", "HackRF (TX), RTL-SDR / HackRF (RX), Nucleo&nbsp;G474RE + Si443x (flight radio)"],
     ["Version", "1.0"],
     ["Date", time.strftime("%Y-%m-%d")]],
    [38 * mm, 132 * mm]))
A(Spacer(1, 8 * mm))
A(callout(
    "Why this guide exists",
    "Orbital velocities (up to ~7.5 km/s) cannot be reproduced by moving the radios. "
    "Instead we shift and sweep the <b>carrier</b> electronically &mdash; the only thing "
    "Doppler does to a non-spread GFSK link &mdash; and measure whether the receiver still "
    "decodes. This guide covers the four tests, the exact app controls, and pass criteria."))
A(PageBreak())

# ---- TOC ---------------------------------------------------------------
A(Paragraph("Contents", S["h1"]))
A(Paragraph(
    "1.&nbsp;&nbsp;Purpose &amp; scope<br/>"
    "2.&nbsp;&nbsp;Background: what Doppler does to the link<br/>"
    "3.&nbsp;&nbsp;Prerequisites &amp; bench setup<br/>"
    "4.&nbsp;&nbsp;The Satellite Doppler card<br/>"
    "5.&nbsp;&nbsp;Test A &mdash; Static offset tolerance<br/>"
    "6.&nbsp;&nbsp;Test B &mdash; Doppler rate robustness<br/>"
    "7.&nbsp;&nbsp;Test C &mdash; Verify the mitigation (RX tracking)<br/>"
    "8.&nbsp;&nbsp;Test D &mdash; SST01 / Si443x flight-radio AFC<br/>"
    "9.&nbsp;&nbsp;Recording results &amp; pass criteria<br/>"
    "10.&nbsp;Limitations<br/>"
    "A.&nbsp;&nbsp;Appendix &mdash; controls &amp; endpoints<br/>"
    "B.&nbsp;&nbsp;Appendix &mdash; formulas", S["toc"]))

# ---- 1 ----------------------------------------------------------------
A(Paragraph("1.&nbsp;&nbsp;Purpose &amp; scope", S["h1"]))
A(P("This guide describes how to characterise and demonstrate the robustness of the GALAMAD "
    "GFSK uplink/downlink in the presence of the Doppler shift and Doppler rate a low-Earth-orbit "
    "satellite imposes. It applies to:"))
A(bullets([
    "the HackRF&nbsp;&rarr;&nbsp;SDR ground link (software modem), and",
    "the Nucleo&nbsp;+&nbsp;Si443x flight radio's receiver (hardware AFC/clock recovery).",
]))
A(P("The objective is to answer two questions with numbers: <b>(1)</b> how much static frequency "
    "error can the receiver tolerate before decoding fails, and <b>(2)</b> how fast a carrier slew "
    "(Hz/s) can it track before failing. Both are compared against the peak values a real pass "
    "produces at 437&nbsp;MHz."))

# ---- 2 ----------------------------------------------------------------
A(Paragraph("2.&nbsp;&nbsp;Background: what Doppler does to the link", S["h1"]))
A(P("For a narrowband, non-spread signal, satellite motion appears entirely as a time-varying "
    "carrier frequency offset:"))
A(code("f_observed(t) = f_carrier &times; (1 &minus; v_r(t)/c)      [v_r = range-rate, +ve receding]\n"
       "Doppler(t)      = &minus; f_carrier &times; v_r(t)/c\n"
       "Doppler rate(t) = &minus; f_carrier &times; a_r(t)/c        [a_r = range acceleration]"))
A(P("Doppler is therefore separable into two effects that stress the receiver differently:"))
A(bullets([
    "<b>Offset</b> &mdash; a static shift (worst near AOS/LOS). At 437&nbsp;MHz this reaches "
    "roughly <b>&plusmn;8&ndash;10&nbsp;kHz</b> for LEO.",
    "<b>Rate</b> &mdash; the carrier <i>slewing</i>. This is what stresses AFC/tracking loops: "
    "peak values are on the order of <b>tens to ~100&nbsp;Hz/s</b> at 437&nbsp;MHz.",
]))
A(callout("Key insight",
          "A static offset tests the receiver's <b>acquisition window</b>; only a <b>moving</b> "
          "carrier tests its <b>tracking</b>. The app reproduces both by injecting the shift/slew "
          "into the transmitted IQ.", COCOA))

# ---- 3 ----------------------------------------------------------------
A(Paragraph("3.&nbsp;&nbsp;Prerequisites &amp; bench setup", S["h1"]))
A(bullets([
    "Web app running (from the canonical symlink path):",
]))
A(code('cd "/home/luwangac/Downloads/PROSat Designs/SDR Apps/GALAMAD-GFSK-Transceiver"\n'
       './start.sh            # then open http://localhost:5000'))
A(bullets([
    "HackRF connected for TX; RTL-SDR or a second HackRF for RX. Select both in the app.",
    "RX gain: LNA&nbsp;24 / VGA&nbsp;48 for a HackRF&ndash;HackRF bench; LNA&nbsp;0 / VGA&nbsp;8 "
    "for the very strong Si443x beacon.",
    "TX power: tx_gain 40 with amp ON (use the <i>Bench</i> preset); the link is freq-independent, "
    "so both radios follow the frequency boxes.",
    "For the flight-radio test: Nucleo flashed with the alternating firmware and the Si443x card "
    "showing <b>AUTO RX/TX</b>.",
    "Set the app <b>freq</b> and <b>rx freq</b> to <b>437.000&nbsp;MHz</b> (the Si443x itself is "
    "programmed near 433.997&nbsp;MHz; use 434.000 when listening to the beacon).",
]))

# ---- 4 ----------------------------------------------------------------
A(Paragraph("4.&nbsp;&nbsp;The Satellite Doppler card", S["h1"]))
A(P("The full-width <b>SATELLITE DOPPLER</b> card is the control surface and the readout. It has "
    "three parts: <i>Satellite &amp; Observer</i>, <i>Pass Prediction &amp; RX Tracking</i>, and "
    "<i>Lab Doppler stress</i>."))
A(Paragraph("4.1&nbsp;&nbsp;Load a satellite", S["h2"]))
A(bullets([
    "Choose a Celestrak <b>group</b> (stations, amateur, weather, cubesat, &hellip;) or type a "
    "<b>NORAD ID</b> and press <b>Fetch TLE</b>. Elements are requested as OMM/JSON, so new "
    "6-digit IDs (e.g. 100796) work; if Celestrak has none, the app falls back to the GALAMAD "
    "tracker (launchedsats.galamad.space).",
    "You can also paste a TLE or an OMM JSON block and press <b>Use pasted TLE</b>.",
]))
A(Paragraph("4.2&nbsp;&nbsp;Set the observer", S["h2"]))
A(bullets([
    "<b>Use my location</b> fills lat/lon/alt from the browser (needs localhost or https).",
    "<b>GALAMAD Studio</b> snaps to 1.34413&nbsp;&deg;N, 103.88648&nbsp;&deg;E (the default).",
    "Or type the coordinates and press <b>Apply observer</b>.",
]))
A(Paragraph("4.3&nbsp;&nbsp;Read the numbers you must reproduce", S["h2"]))
A(bullets([
    "Set the <b>Downlink</b> frequency (MHz) and <b>Min elevation</b>, then <b>Predict next "
    "pass</b>. The plot is Doppler (Hz) vs time across the pass.",
    "The live line shows the current <b>elevation, azimuth, range, range-rate, Doppler (Hz / ppm) "
    "and Doppler rate (Hz/s)</b> for the tracked satellite. <b>Note the peak |Doppler| and peak "
    "|rate|</b> &mdash; these are your Test A and Test B targets.",
    "<b>Track RX</b> retunes the receiver to follow the predicted downlink (mitigation under test).",
]))

# ---- 5 ----------------------------------------------------------------
A(Paragraph("5.&nbsp;&nbsp;Test A &mdash; Static offset tolerance", S["h1"]))
A(P("<b>Measures:</b> the receiver's frequency-acquisition window / AFC pull-in. Emulates a "
    "frozen Doppler (a single moment of a pass)."))
A(Paragraph("Procedure", S["h2"]))
A(bullets([
    "RX at 437.000&nbsp;MHz; leave <b>Track RX OFF</b>.",
    "In <i>Lab Doppler stress</i>, set <b>Rate&nbsp;Hz/s = 0</b> and <b>Offset&nbsp;Hz = 0</b>; "
    "press <b>Apply to TX</b>, then <b>Send</b>. Capture &amp; Decode &rarr; record the decoded "
    "payload / CRC-valid count (baseline).",
    "Repeat for offset = &plusmn;500, &plusmn;1000, &plusmn;2000, &plusmn;5000, &plusmn;8000, "
    "&plusmn;10000&nbsp;Hz. Press <b>Send</b> after each change.",
    "Tabulate CRC-valid decode vs injected offset.",
]))
A(callout("Equivalent alternative",
          "Instead of injecting on TX you may offset the <b>RX</b> frequency box by the same "
          "amount; this additionally exercises the receiver's tuning accuracy. Pick one method "
          "and be consistent."))

# ---- 6 ----------------------------------------------------------------
A(Paragraph("6.&nbsp;&nbsp;Test B &mdash; Doppler rate robustness", S["h1"]))
A(P("<b>Measures:</b> how fast a carrier slew the receiver can acquire and track. This is the "
    "decisive test &mdash; a pass fails only if the rate exceeds tolerance."))
A(Paragraph("Procedure", S["h2"]))
A(bullets([
    "Leave <b>Track RX OFF</b>. Set <b>Offset&nbsp;Hz = 0</b>.",
    "Set <b>Rate&nbsp;Hz/s</b> to 10, 25, 50, 100, 200&nbsp;Hz/s in turn. Press <b>Apply to TX</b> "
    "then <b>Send</b>; Capture &amp; Decode after each.",
    "The carrier slews continuously; each loop sweeps (rate &times; loop&nbsp;duration) Hz. Use "
    "<b>Use predicted</b> to load the exact rate from the live pass.",
    "Record decode success vs rate; the highest rate that still yields CRC-valid packets is the "
    "tracking limit.",
]))
A(callout("Interpreting the sweep",
          "The generated file loops, so the sweep restarts each pass. The phase discontinuity "
          "falls inside the silent inter-packet gap, so it does not disturb the receiver.", COCOA))

# ---- 7 ----------------------------------------------------------------
A(Paragraph("7.&nbsp;&nbsp;Test C &mdash; Verify the mitigation (RX tracking)", S["h1"]))
A(P("<b>Measures:</b> that the software Doppler compensation actually cancels the shift."))
A(bullets([
    "Load a satellite and observer; confirm the live Doppler in the card is non-zero.",
    "Enable <b>Track RX</b>. The status shows <i>RX tuned &lt;f&gt;&nbsp;MHz</i>; verify it moves "
    "as the prediction changes.",
    "For a closed-loop demonstration, inject a <b>static</b> offset on TX while Track RX is on "
    "and configured for the same pass; the receiver re-centres on the slewed carrier and decodes.",
    "Compare the decode-rate limit from Test B with tracking on vs off &mdash; the improvement "
    "quantifies the benefit of compensation.",
]))

# ---- 8 ----------------------------------------------------------------
A(Paragraph("8.&nbsp;&nbsp;Test D &mdash; SST01 / Si443x flight-radio AFC", S["h1"]))
A(P("<b>Measures:</b> the flight radio's own AFC/clock-recovery margin, with no software help."))
A(bullets([
    "Flash the alternating firmware; confirm the Si443x card reads <b>AUTO RX/TX</b> and the "
    "log shows <i>Si443x TX / Si443x RX</i> lines.",
    "Drive the HackRF (UI <b>Send</b>) at 434.000&nbsp;MHz so the Si443x RX window receives it.",
    "Inject <b>Offset&nbsp;Hz</b> = the model's peak |Doppler| (e.g. &plusmn;9&nbsp;kHz); watch "
    "the <b>Si443x Transceiver Log</b> for <i>PAYLOAD:</i> lines. Sweep the offset to find where "
    "the Si443x stops decoding &mdash; its AFC pull-in.",
    "Repeat with a <b>Rate&nbsp;Hz/s</b> sweep to test whether its AFC tracks a moving carrier.",
]))

# ---- 9 ----------------------------------------------------------------
A(Paragraph("9.&nbsp;&nbsp;Recording results &amp; pass criteria", S["h1"]))
A(table(
    ["Test", "Sweep", "Metric", "Pass criterion"],
    [["A", "Static offset &plusmn;0&ndash;10 kHz", "Max offset with CRC-valid decode",
      "&ge; peak |Doppler| of target pass"],
     ["B", "Rate 0&ndash;200 Hz/s", "Max rate with CRC-valid decode",
      "&ge; peak |Doppler rate| of target pass"],
     ["C", "Tracking on/off", "Rate limit improvement", "Tracking restores margin at peak rate"],
     ["D", "Offset / rate (Si443x)", "Max offset/rate with PAYLOAD",
      "&ge; peak values for the mission orbit"]],
    [16 * mm, 44 * mm, 55 * mm, 55 * mm]))
A(Spacer(1, 3 * mm))
A(callout("Green light",
          "The link is considered Doppler-robust when, for the worst-case pass of the mission "
          "orbit, both the peak offset and the peak rate are decoded with CRC valid &mdash; "
          "either open-loop (radio tolerance) or with RX tracking enabled.", GREEN))

# ---- 10 ---------------------------------------------------------------
A(Paragraph("10.&nbsp;&nbsp;Limitations", S["h1"]))
A(bullets([
    "<b>Ideal channel.</b> Doppler is injected at baseband; there is no range-delay, multipath or "
    "path-loss model. This is correct for isolating Doppler (a non-spread GFSK PHY sees it purely "
    "as carrier offset + rate).",
    "<b>Loop length.</b> A constant rate reproduces the steep segment of a pass, not the whole "
    "S-curve. For a full pass, extend the file length or request the predicted-curve IQ mode.",
    "<b>Altitude.</b> Browser geolocation altitude is coarse; it has negligible effect on "
    "topocentric range-rate at these ranges and may be left at the default.",
    "<b>Element age.</b> SGP4 accuracy degrades with TLE age; for precise passes use elements "
    "less than ~1 day old.",
]))

# ---- A ----------------------------------------------------------------
A(PageBreak())
A(Paragraph("Appendix A &mdash; Controls &amp; endpoints", S["h1"]))
A(table(
    ["Control / endpoint", "Purpose"],
    [["Fetch TLE / group / NORAD ID", "Load elements (Celestrak OMM, GALAMAD fallback)"],
     ["Use my location / GALAMAD Studio", "Set the observer for topocentric geometry"],
     ["Predict next pass", "AOS/TCA/LOS, max elevation, max Doppler, curve"],
     ["Track RX", "Retune the receiver to follow the predicted downlink"],
     ["Lab Doppler stress: Offset Hz", "Constant carrier offset injected into TX IQ"],
     ["Lab Doppler stress: Rate Hz/s", "Linear carrier slew injected into TX IQ"],
     ["Apply to TX / Use predicted / Clear", "Set, auto-fill from live pass, or zero the injection"],
     ["POST /doppler/tle", "Load elements (group | catnr | text)"],
     ["POST /doppler/observer", "Set lat/lon/alt"],
     ["POST /doppler/config", "Downlink frequency, elevation mask"],
     ["POST /doppler/inject", "Set TX Doppler offset &amp; rate (off_hz, rate_hz_s)"],
     ["GET /doppler/status", "Live look, Doppler, Doppler rate, inject state"],
     ["GET /doppler/predict", "Next-pass prediction samples"]],
    [68 * mm, 102 * mm]))

# ---- B ----------------------------------------------------------------
A(Paragraph("Appendix B &mdash; Formulas", S["h1"]))
A(code("Doppler shift      f_d = &minus; f_c &middot; v_r / c           (v_r = line-of-sight range-rate)\n"
       "Doppler rate       &nbsp;f_d' = &minus; f_c &middot; a_r / c        (a_r = range acceleration)\n"
       "Injected phase     &phi;(t) = 2&pi;( f_0&middot;t + &frac12;&middot;k&middot;t&sup2; )   "
       "(f_0 = offset, k = rate)\n"
       "Per-loop sweep     &Delta;f = k &middot; (loop duration)"))
A(P("Constants: c = 299&nbsp;792&nbsp;458&nbsp;m/s. WGS-84 ellipsoid for observer/topocentric "
    "geometry. SGP4 propagation of the loaded element set."))
A(Spacer(1, 4 * mm))
A(Paragraph("&mdash; end &mdash;", ParagraphStyle("end", parent=S["note"], alignment=TA_CENTER)))

# --------------------------------------------------------------------------
def _decorate(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, A4[0], A4[1] - 8, fill=1, stroke=0)
    canvas.setFillColor(GOLD2)
    canvas.rect(0, A4[1] - 6, A4[0], 6, fill=1, stroke=0)
    if doc.page > 1:
        canvas.setFillColor(MUT)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(20 * mm, 12 * mm, "GALAMAD Aerospace \u2014 GFSK Transceiver Doppler Test Guide")
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, "Page %d" % doc.page)
        canvas.setStrokeColor(LINE)
        canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
    canvas.restoreState()


doc = BaseDocTemplate(OUT, pagesize=A4,
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm,
                      title="GALAMAD GFSK Doppler Robustness Lab Test Guide",
                      author="GALAMAD Aerospace")
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=_decorate)])
doc.build(content)
print("wrote", OUT)
