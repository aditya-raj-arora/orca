#!/usr/bin/env python3
"""
adapter_reliability_check.py — the pre-demo "are the data adapters healthy?"
gate for issue #44.

Owner: P3. Requirements: FR-WX-1..4, FR-OCEAN-1..4, NFR-REL-1/2. LLD §2.9.

Unlike scripts/e2e_live_check.py this needs NO LLM key and NO database — it
only exercises the three external-data adapters (Open-Meteo forecast + marine,
WeatherAPI, GDACS via WeatherDataAdapter; INCOIS GeoServer WFS + WMS via
INCOISAdapter; bundled boundary GeoJSON via GISBoundaryAdapter) for each demo
location and checks the result is 'ok' with sane values.

Run it from wherever you want the answer for — locally, or on the deployed
Render backend's shell to satisfy "reachable from the deployed environment":

    cd src/backend
    python ../../scripts/adapter_reliability_check.py

Exit code: 0 iff every location's weather adapter is 'ok' with sane wind/wave
and the INCOIS + boundary sources are reachable. A 'stale' PFZ (snapshot
fallback) or unpublished SST/chl (None) is reported, not failed — that is the
adapters behaving correctly per FR-OCEAN-2 / NFR-REL-1, not an outage.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "src" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.data_access.gis_boundary_adapter import GISBoundaryAdapter  # noqa: E402
from app.data_access.incois_adapter import INCOISAdapter  # noqa: E402
from app.data_access.weather_adapter import WeatherDataAdapter  # noqa: E402

# The three reference locations used across all P3 work (issues #10 / #15).
LOCATIONS = [
    ("Kochi", 9.93, 76.26),
    ("Chennai", 13.08, 80.27),
    ("Kollam", 8.88, 76.60),
]

# Sanity envelopes — deliberately wide: this catches "provider returned
# garbage / wrong units", not "unusual weather".
WIND_KMH_MAX = 250.0
WAVE_M_MAX = 25.0
SST_C_RANGE = (10.0, 40.0)
CHL_RANGE = (0.0, 100.0)


def _rule(c: str = "-") -> str:
    return c * 72


def _check_weather(lat: float, lon: float) -> tuple[bool, str]:
    res = WeatherDataAdapter().fetch({"lat": lat, "lon": lon, "window": None})
    if res.status != "ok" or not res.data:
        return False, f"status={res.status} (upstream unreachable)"
    wind = res.data.get("wind_speed_kmh")
    wave = res.data.get("wave_height_m")
    src = res.data.get("forecast_source")
    alerts_ok = res.data.get("alerts_source_available")
    bad = []
    if not isinstance(wind, (int, float)) or not (0 <= wind <= WIND_KMH_MAX):
        bad.append(f"wind={wind!r}")
    if not isinstance(wave, (int, float)) or not (0 <= wave <= WAVE_M_MAX):
        bad.append(f"wave={wave!r}")
    note = f"wind={wind} km/h  wave={wave} m  forecast_source={src}"
    if not alerts_ok:
        note += "  [alert sources unreachable — active_alerts is 'unknown', not 'clear']"
    return (not bad), (note if not bad else f"insane values: {', '.join(bad)} | {note}")


def _check_incois_pfz() -> tuple[bool, str]:
    res = INCOISAdapter().fetch({"kind": "pfz"})
    if res.status == "unavailable":
        return False, "status=unavailable (live WFS down AND no usable snapshot)"
    n = (res.data or {}).get("count")
    if res.status == "stale":
        return True, f"status=stale — serving bundled snapshot, {n} zones (refresh it before demo)"
    return True, f"status=ok — live WFS, {n} PFZ advisory lines"


def _check_incois_ocean_params(lat: float, lon: float) -> tuple[bool, str]:
    res = INCOISAdapter().fetch({"kind": "ocean_params", "lat": lat, "lon": lon})
    if res.status == "unavailable":
        return False, "status=unavailable (GeoServer WMS unreachable)"
    sst = (res.data or {}).get("sst_c")
    chl = (res.data or {}).get("chlorophyll_mg_m3")
    for label, val, (lo, hi) in (("sst", sst, SST_C_RANGE), ("chl", chl, CHL_RANGE)):
        if val is not None and not (lo <= val <= hi):
            return False, f"insane {label}={val!r} (outside {lo}..{hi})"
    # None is legitimate: INCOIS simply doesn't publish a value for that pixel
    # today (FR-OCEAN-2). Reachable + parseable is the bar here.
    return True, f"status=ok — sst={sst}  chl={chl}  (None = not published, per FR-OCEAN-2)"


def _check_boundaries() -> tuple[bool, str]:
    res = GISBoundaryAdapter().fetch({"type": "MPA"})
    if res.status != "ok" or not res.data:
        return False, f"status={res.status} (bundled boundary GeoJSON missing / unreadable)"
    mpa = len(res.data["features"])
    imbl = len(GISBoundaryAdapter().fetch({"type": "IMBL"}).data["features"])
    return (mpa > 0 and imbl > 0), f"MPA features={mpa}  IMBL features={imbl}"


def main() -> int:
    print(_rule("="))
    print("ORCA adapter reliability check (#44) — no LLM / DB, data adapters only")
    print(_rule("="))

    passed = True

    # Location-independent sources first.
    for label, (ok, note) in {
        "INCOIS PFZ (WFS)": _check_incois_pfz(),
        "GIS boundaries (bundled)": _check_boundaries(),
    }.items():
        passed &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {label:<26} {note}")

    for name, lat, lon in LOCATIONS:
        print(f"\n{name} ({lat}, {lon})")
        for label, (ok, note) in {
            "weather (Open-Meteo/WAPI/GDACS)": _check_weather(lat, lon),
            "INCOIS SST/chl (WMS)": _check_incois_ocean_params(lat, lon),
        }.items():
            passed &= ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {label:<32} {note}")

    print(f"\n{_rule('=')}")
    print(f"RESULT: {'PASS — adapters healthy for the demo' if passed else 'FAIL — see above'}")
    print(_rule("="))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
