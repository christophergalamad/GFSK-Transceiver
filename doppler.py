#!/usr/bin/env python3
"""Satellite Doppler model for the GALAMAD GFSK transceiver.

Fetches a real TLE from Celestrak, propagates it with SGP4, and computes the
topocentric range-rate toward an observer so we can predict and (optionally)
compensate the Doppler shift on the RX downlink.

No third-party deps beyond `sgp4` and `numpy`.

Doppler sign convention (matches SDR practice):
    f_observed = f_carrier * (1 - range_rate / c)
    -> range_rate < 0 (approaching) => Doppler > 0 => RX must tune HIGHER.
    `doppler_hz(...)` returns the shift to ADD to the nominal carrier.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4 import omm
from sgp4.api import Satrec, jday

C_LIGHT = 299_792_458.0          # m/s
OMEGA_E = 7.292115e-5            # rad/s, Earth rotation rate
WGS84_A = 6_378_137.0            # m
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
GALAMAD = "https://launchedsats.galamad.space"    # GALAMAD's own tracker (OMM/GP JSON)
UA = {"User-Agent": "GALAMAD-GFSK/1.0 (satellite doppler; christopher.coder@galamad.space)"}

GROUPS = ["stations", "amateur", "weather", "science", "cubesat",
          "galileo", "gps-ops", "oneweb", "starlink"]

_GALAMAD_CACHE = {"ts": 0.0, "sats": []}
_GALAMAD_TTL = 300.0             # seconds


# --------------------------------------------------------------------------
# TLE fetch / parse
# --------------------------------------------------------------------------
def fetch_tle(group: str = None, catnr: int = None, timeout: float = 15.0) -> str:
    """Return raw TLE text from Celestrak (by group or by NORAD catalog number).

    NOTE: Celestrak has retired the classic TLE format for catalog numbers
    > 99999 (the alpha-5 scheme is deprecated), so this 404s for those objects.
    Prefer `fetch_gp()` (OMM/JSON), which works for all catalog numbers.
    """
    if catnr:
        url = f"{CELESTRAK}?CATNR={int(catnr)}&FORMAT=TLE"
    else:
        url = f"{CELESTRAK}?GROUP={group or 'stations'}&FORMAT=TLE"
    req = urllib.request.Request(url, headers=UA)
    txt = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    txt = txt.strip()
    if not txt or "No GP data found" in txt or txt.startswith("No TLE"):
        raise ValueError("Celestrak returned no TLE")
    return txt


def fetch_gp(catnr=None, group: str = None, name: str = None, timeout: float = 15.0) -> list:
    """Fetch OMM/GP element set(s) from Celestrak as JSON.

    Works for 5- and 6-digit catalog numbers (unlike the TLE format). Returns a
    list of gp dicts (CCSDS OMM), each consumable by `Doppler(gp=...)`.
    """
    if catnr:
        q = "CATNR=" + str(int(catnr))
    elif name:
        q = "NAME=" + urllib.parse.quote(name)
    else:
        q = "GROUP=" + urllib.parse.quote(group or "stations")
    url = f"{CELESTRAK}?{q}&FORMAT=JSON"
    req = urllib.request.Request(url, headers=UA)
    txt = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace").strip()
    if not txt or "No GP data" in txt:
        raise ValueError("Celestrak returned no GP data")
    data = json.loads(txt)
    if not isinstance(data, list) or not data:
        raise ValueError("Celestrak returned no GP data")
    return data


def parse_tle(text: str, want: str = None):
    """Parse a (possibly multi-satellite) TLE block.

    Returns (name, line1, line2). If `want` (name substring, case-insensitive)
    is given, picks the first matching satellite, else the first entry.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    entries = []
    i = 0
    while i < len(lines):
        # name line (optional) followed by two element lines
        if lines[i].startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            entries.append(("(unnamed)", lines[i], lines[i + 1]))
            i += 2
        elif i + 2 < len(lines) and lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            entries.append((lines[i].strip(), lines[i + 1], lines[i + 2]))
            i += 3
        else:
            i += 1
    if not entries:
        raise ValueError("no valid TLE found in text")
    if want:
        w = want.strip().lower()
        for e in entries:
            if w in e[0].lower():
                return e
    return entries[0]


def fetch_galamad(catnr=None, name=None, timeout: float = 20.0):
    """Look up an element set on the GALAMAD tracker (launchedsats.galamad.space).

    Returns (name, gp_dict) where gp_dict is a CCSDS OMM / Celestrak-GP dict
    (the same shape SGP4's `omm.initialize` consumes). Covers freshly launched
    objects that Celestrak has not catalogued yet.
    """
    now = time.time()
    if now - _GALAMAD_CACHE["ts"] > _GALAMAD_TTL or not _GALAMAD_CACHE["sats"]:
        req = urllib.request.Request(GALAMAD + "/api/satellites", headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))
        _GALAMAD_CACHE["sats"] = data.get("satellites", [])
        _GALAMAD_CACHE["ts"] = now
    sats = _GALAMAD_CACHE["sats"]
    if catnr is not None:
        c = int(catnr)
        for s in sats:
            try:
                if int(s.get("noradId", -1)) == c and s.get("gp"):
                    return s.get("name") or "SAT", s["gp"]
            except (TypeError, ValueError):
                pass
    if name:
        w = str(name).strip().lower()
        for s in sats:
            if w and w in str(s.get("name", "")).lower() and s.get("gp"):
                return s.get("name") or "SAT", s["gp"]
    raise ValueError(f"GALAMAD tracker has no object for {catnr if catnr is not None else name}")


# --------------------------------------------------------------------------
# Time / frames
# --------------------------------------------------------------------------
def _jd(dt: datetime):
    """UTC datetime -> (jd, fr) pair for sgp4."""
    sec = dt.second + dt.microsecond * 1e-6
    return jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, sec)


def gmst_rad(jd: float) -> float:
    """Greenwich Mean Sidereal Time (radians), IAU-82 style (good to <1e-4 rad)."""
    d = jd - 2451545.0
    T = d / 36525.0
    g = 280.46061837 + 360.98564736629 * d + 0.000387933 * T * T - T ** 3 / 38710000.0
    return math.radians(g % 360.0)


def _ecef(lat_deg: float, lon_deg: float, alt_m: float) -> np.ndarray:
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sp, cp = math.sin(lat), math.cos(lat)
    N = WGS84_A / math.sqrt(1 - WGS84_E2 * sp * sp)
    return np.array([
        (N + alt_m) * cp * math.cos(lon),
        (N + alt_m) * cp * math.sin(lon),
        (N * (1 - WGS84_E2) + alt_m) * sp,
    ])                                              # meters, ECEF


def _eci_from_ecef(vec_ecef: np.ndarray, g: float) -> np.ndarray:
    c, s = math.cos(g), math.sin(g)
    x, y, z = vec_ecef
    return np.array([c * x - s * y, s * x + c * y, z])   # meters, ECI/TEME


def _ecef_from_eci(vec_eci: np.ndarray, g: float) -> np.ndarray:
    c, s = math.cos(g), math.sin(g)
    x, y, z = vec_eci
    return np.array([c * x + s * y, -s * x + c * y, z])


# --------------------------------------------------------------------------
# Core model
# --------------------------------------------------------------------------
class Doppler:
    """SGP4 satellite + observer; computes look angles and Doppler shift.

    Accepts either a classic TLE (line1/line2) or an OMM/GP dict (`gp=`),
    the latter being what Celestrak's JSON and the GALAMAD tracker return.
    """

    def __init__(self, line1: str = None, line2: str = None, name: str = "SAT",
                 lat=5.6037, lon=-0.1870, alt=50.0, gp: dict = None):
        if gp is not None:
            self.sat = Satrec()
            omm.initialize(self.sat, gp)
        elif line1 and line2:
            self.sat = Satrec.twoline2rv(line1, line2)
        else:
            raise ValueError("Doppler needs either a TLE (line1/line2) or a gp dict")
        self.name = name
        self.obs = (float(lat), float(lon), float(alt))

    def set_observer(self, lat, lon, alt):
        self.obs = (float(lat), float(lon), float(alt))

    def look(self, dt: datetime, f_carrier: float = 437_000_000.0) -> dict:
        """Topocentric geometry + Doppler at UTC instant `dt`."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        jd, fr = _jd(dt)
        e, r, v = self.sat.sgp4(jd, fr)      # km, km/s (TEME)
        if e != 0:
            raise RuntimeError(f"SGP4 error {e}")
        g = gmst_rad(jd + fr)
        r_m = np.array(r) * 1000.0           # ECI, meters
        v_m = np.array(v) * 1000.0           # ECI, m/s

        obs_ecef = _ecef(*self.obs)
        obs_eci = _eci_from_ecef(obs_ecef, g)
        v_obs = np.cross([0.0, 0.0, OMEGA_E], obs_eci)   # ECI observer velocity

        # Range vector & range-rate (topocentric, ECI)
        rho_v = r_m - obs_eci
        rho = float(np.linalg.norm(rho_v))
        rr_v = v_m - v_obs
        range_rate = float(np.dot(rho_v, rr_v) / rho)     # m/s (+ = receding)

        # ENU for elevation/azimuth (do it in ECEF to avoid frame bookkeeping)
        sat_ecef = _ecef_from_eci(r_m, g)
        dr = sat_ecef - obs_ecef
        lat, lon = math.radians(self.obs[0]), math.radians(self.obs[1])
        east = np.array([-math.sin(lon), math.cos(lon), 0.0])
        north = np.array([-math.sin(lat) * math.cos(lon),
                          -math.sin(lat) * math.sin(lon), math.cos(lat)])
        up = np.array([math.cos(lat) * math.cos(lon),
                       math.cos(lat) * math.sin(lon), math.sin(lat)])
        E, N, U = float(np.dot(dr, east)), float(np.dot(dr, north)), float(np.dot(dr, up))
        el = math.degrees(math.atan2(U, math.hypot(E, N)))
        az = math.degrees(math.atan2(E, N)) % 360.0

        dop = -range_rate / C_LIGHT * f_carrier
        return {
            "t": dt,
            "lat_sat": math.degrees(math.asin(r_m[2] / np.linalg.norm(r_m))),
            "elev_deg": el,
            "azim_deg": az,
            "range_km": rho / 1000.0,
            "range_rate_mps": range_rate,
            "doppler_hz": dop,
            "doppler_ppm": dop / f_carrier * 1e6 if f_carrier else 0.0,
            "visible": el > 0.0,
        }

    def predict_pass(self, f_carrier: float = 437_000_000.0, mask_deg: float = 5.0,
                     t0: datetime = None, horizon_min: float = 720.0,
                     scan_s: float = 20.0, step_s: float = 5.0) -> dict:
        """Find the next pass above `mask_deg` and return a fine Doppler curve.

        Returns a dict with `passes` (brief list) and `next` (the first/current
        pass with `samples` for plotting). If nothing rises in the horizon, the
        fields are None/empty.
        """
        if t0 is None:
            t0 = datetime.now(timezone.utc)
        # 1) coarse scan for the first window above the mask
        start = None
        t = t0
        end_h = t0 + timedelta(minutes=horizon_min)
        prev = None
        while t <= end_h:
            try:
                el = self.look(t, f_carrier)["elev_deg"]
            except Exception:
                el = -90.0
            if el > mask_deg:
                if start is None:
                    start = t
                prev = t
            elif start is not None:
                break
            t += timedelta(seconds=scan_s)
        if start is None:
            return {"next": None, "passes": []}

        aos = _refine(self, t0 if start - t0 < timedelta(seconds=scan_s) else
                      start - timedelta(seconds=scan_s), start, f_carrier, mask_deg, True)
        # find LOS
        t = start
        los = None
        while t <= end_h:
            try:
                el = self.look(t, f_carrier)["elev_deg"]
            except Exception:
                el = -90.0
            if el <= mask_deg and t > start + timedelta(seconds=scan_s):
                los = _refine(self, t - timedelta(seconds=scan_s), t, f_carrier, mask_deg, False)
                break
            los = t
            t += timedelta(seconds=scan_s)
        if los is None:
            los = prev or start

        # 2) fine samples for the curve + TCA/max
        samples, tca, max_el, max_dop = [], None, None, None
        t = aos
        while t <= los:
            try:
                L = self.look(t, f_carrier)
                samples.append({"t": L["t"].isoformat(), "el": L["elev_deg"],
                                "doppler_hz": L["doppler_hz"], "range_km": L["range_km"],
                                "azim_deg": L["azim_deg"]})
                if max_el is None or L["elev_deg"] > max_el:
                    max_el, tca = L["elev_deg"], L["t"]
                if max_dop is None or abs(L["doppler_hz"]) > abs(max_dop):
                    max_dop = L["doppler_hz"]
            except Exception:
                pass
            t += timedelta(seconds=step_s)

        now = datetime.now(timezone.utc)
        nxt = {
            "name": self.name,
            "aos": aos.isoformat(),
            "los": los.isoformat(),
            "tca": tca.isoformat() if tca else None,
            "aos_in_s": (aos - now).total_seconds(),
            "los_in_s": (los - now).total_seconds(),
            "duration_s": (los - aos).total_seconds(),
            "max_elev_deg": max_el,
            "max_doppler_hz": max_dop,
            "mask_deg": mask_deg,
            "samples": samples,
        }
        return {"next": nxt, "passes": [{"aos": nxt["aos"], "los": nxt["los"],
                                         "max_elev_deg": max_el}]}


def _refine(model: Doppler, lo: datetime, hi: datetime, f_carrier: float,
            mask_deg: float, rising: bool, iters: int = 18) -> datetime:
    """Bisect the mask-crossing time between lo and hi."""
    for _ in range(iters):
        mid = lo + (hi - lo) / 2
        try:
            el = model.look(mid, f_carrier)["elev_deg"]
        except Exception:
            el = -90.0
        above = el > mask_deg
        if above == rising:
            hi = mid
        else:
            lo = mid
    return lo + (hi - lo) / 2


# --------------------------------------------------------------------------
# CLI smoke test:  python3 doppler.py [group|CATNR]
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "stations"
    gp = (fetch_gp(catnr=int(arg)) if arg.isdigit() else fetch_gp(group=arg))[0]
    name = gp.get("OBJECT_NAME", "SAT")
    d = Doppler(gp=gp, name=name)      # default observer: Accra, Ghana
    L = d.look(datetime.now(timezone.utc))
    print(f"{name}: el={L['elev_deg']:.1f} az={L['azim_deg']:.1f} "
          f"range={L['range_km']:.1f} km rr={L['range_rate_mps']:.1f} m/s "
          f"doppler={L['doppler_hz']:.1f} Hz @437MHz")
    plan = d.predict_pass()
    n = plan["next"]
    if n:
        print(f"next pass aos={n['aos']} los={n['los']} "
              f"max_el={n['max_elev_deg']:.1f} max_dop={n['max_doppler_hz']:.0f} Hz "
              f"({len(n['samples'])} samples)")
    else:
        print("no pass above mask in horizon")
