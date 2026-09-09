#!/usr/bin/env python3
"""
geofence_risk_sanity_check.py — the pre-demo "is geofencing/Risk correct?"
gate for issue #45.

Owner: P4. Requirements: FR-GEO-1..4, FR-RISK-1..3. LLD §2.5, §3, §4.2/Fig.2.

FINDING THIS SCRIPT SURFACES (see docs/p4-geofence-risk-sanity-45.md for the
full writeup): GeofencingAgent never reads the Postgres `geofence_boundary`
table. GISBoundaryAdapter.fetch() (app/data_access/gis_boundary_adapter.py)
loads exclusively from the static GeoJSON at GIS_BOUNDARY_DATA_PATH — the DB
table + app/db/load_boundaries.py seeder exist (per the original LLD §3
design) but nothing in the live request path queries them. So "PostGIS
enabled and geofence_boundary seeded in prod" (issue #45's stated AC) is not
what demo-day correctness actually depends on; what matters is the GeoJSON
file shipping in the Docker image with the right features, which is what
this script checks instead.

Needs NO LLM key and NO database — same shape as scripts/adapter_reliability_check.py
(issue #44). Exercises the real bundled GeoJSON (not test fixtures) through
GeofencingAgent.check() and RiskSafetyAgent.evaluate() for:
  - every demo location (expected: clear / far from any boundary)
  - one known point inside Gulf of Mannar MPA (expected: within_mpa=True,
    Risk verdict UNSAFE, unconditionally per FR-GEO-4)
  - one known point near the IMBL buffer (expected: within_imbl_buffer=True,
    Risk verdict UNSAFE)

Run it from wherever you want the answer for — locally, or on the deployed
Render backend's shell to confirm the shipped image actually has the file:

    python scripts/geofence_risk_sanity_check.py

Exit code: 0 iff every case matches its expected in/near/clear outcome and
the derived Risk verdict is correct for the known-safe and known-unsafe cases.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "src" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.agents.geofencing_agent import GeofencingAgent  # noqa: E402
from app.agents.risk_safety_agent import RiskSafetyAgent  # noqa: E402
from app.data_access.gis_boundary_adapter import GISBoundaryAdapter  # noqa: E402
from app.schemas.common import LatLon  # noqa: E402
from app.schemas.weather import WeatherResult  # noqa: E402

# Same three reference locations #44 used (issues #10 / #15) — open water,
# expected clear of every boundary.
DEMO_LOCATIONS = [
    ("Kochi", 9.93, 76.26),
    ("Chennai", 13.08, 80.27),
    ("Kollam", 8.88, 76.60),
]

# A representative interior point of the Gulf of Mannar Marine Biosphere
# Reserve polygon in the bundled GeoJSON (computed via
# shapely.representative_point(), not just a boundary vertex).
KNOWN_UNSAFE_MPA = ("Gulf of Mannar (inside MPA)", 9.1861, 78.8919)

# A vertex read directly off one of the bundled IMBL LineString segments
# (data/gis/imbl_mpa_boundaries.geojson) — trivially within IMBL_BUFFER_KM
# since distance-to-line is ~0 there. Read at runtime in main() rather than
# hardcoded, so this always tracks whatever the bundled file actually
# contains instead of a coordinate that could silently drift from it.


def _rule(c: str = "-") -> str:
    return c * 78


def _first_imbl_vertex() -> tuple[float, float] | None:
    """(lat, lon) of the first vertex of the first IMBL LineString feature in
    the bundled GeoJSON — read at runtime so this always tracks whatever the
    file actually contains rather than a coordinate that could drift."""
    import json

    from app.core.config import get_settings

    path = Path(get_settings().gis_boundary_data_path)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    with path.open("r", encoding="utf-8") as f:
        geojson = json.load(f)
    for feature in geojson.get("features", []):
        if feature.get("properties", {}).get("type") != "IMBL":
            continue
        coords = feature["geometry"]["coordinates"]
        while isinstance(coords[0], list):
            coords = coords[0]
        lon, lat = coords[0], coords[1]
        return (lat, lon)
    return None


def _calm_weather() -> WeatherResult:
    """A deliberately calm, fully-available WeatherResult so any UNSAFE
    verdict below is attributable ONLY to geofencing (FR-GEO-4: a violation
    is non-negotiable and must not depend on weather), and any SAFE verdict
    is attributable only to being clear of one."""
    from datetime import UTC, datetime

    return WeatherResult(
        status="ok",
        wind_speed_kmh=10.0,
        wave_height_m=0.5,
        visibility_m=10000.0,
        active_alerts=[],
        alerts_source_available=True,
        data_timestamp=datetime.now(UTC),
    )


def main() -> int:
    agent = GeofencingAgent(GISBoundaryAdapter())
    risk = RiskSafetyAgent()
    weather = _calm_weather()
    failures: list[str] = []

    print(_rule("="))
    print("Issue #45 — geofence + Risk sanity check (real bundled GeoJSON)")
    print(_rule("="))

    print("\n-- Demo locations (expect: clear of every boundary) --")
    for name, lat, lon in DEMO_LOCATIONS:
        result = agent.check(LatLon(lat=lat, lon=lon))
        if result is None:
            failures.append(f"{name}: geofence check returned None (adapter unavailable)")
            print(f"  [FAIL] {name}: adapter unavailable")
            continue
        verdict = risk.evaluate(weather, result, None)
        ok = not result.within_mpa and not result.within_imbl_buffer and verdict.verdict == "SAFE"
        status = "OK" if ok else "FAIL"
        print(
            f"  [{status}] {name} ({lat}, {lon}): within_mpa={result.within_mpa} "
            f"within_imbl_buffer={result.within_imbl_buffer} "
            f"imbl_distance_km={result.imbl_distance_km:.1f} -> verdict={verdict.verdict}"
        )
        if not ok:
            failures.append(f"{name}: expected clear/SAFE, got {result} / {verdict.verdict}")

    print("\n-- Known-unsafe: inside Gulf of Mannar MPA --")
    name, lat, lon = KNOWN_UNSAFE_MPA
    result = agent.check(LatLon(lat=lat, lon=lon))
    if result is None:
        failures.append(f"{name}: geofence check returned None")
        print(f"  [FAIL] {name}: adapter unavailable")
    else:
        verdict = risk.evaluate(weather, result, None)
        ok = result.within_mpa and verdict.verdict == "UNSAFE"
        status = "OK" if ok else "FAIL"
        print(
            f"  [{status}] {name} ({lat}, {lon}): within_mpa={result.within_mpa} "
            f"mpa_name={result.mpa_name!r} -> verdict={verdict.verdict} "
            f"(calm weather — verdict must come from the geofence alone, FR-GEO-4)"
        )
        if not ok:
            failures.append(f"{name}: expected within_mpa=True/UNSAFE, got {result} / {verdict.verdict}")

    print("\n-- Known-unsafe: a point directly on an IMBL segment --")
    imbl_point = _first_imbl_vertex()
    if imbl_point is None:
        failures.append("no IMBL feature/vertex found in the bundled GeoJSON")
        print("  [FAIL] no IMBL feature found in the bundled file")
    else:
        lat, lon = imbl_point
        result = agent.check(LatLon(lat=lat, lon=lon))
        if result is None:
            failures.append("IMBL vertex point: geofence check returned None")
            print("  [FAIL] adapter unavailable")
        else:
            verdict = risk.evaluate(weather, result, None)
            ok = result.within_imbl_buffer and verdict.verdict == "UNSAFE"
            status = "OK" if ok else "FAIL"
            print(
                f"  [{status}] IMBL vertex ({lat:.4f}, {lon:.4f}): "
                f"within_imbl_buffer={result.within_imbl_buffer} "
                f"imbl_distance_km={result.imbl_distance_km:.2f} -> verdict={verdict.verdict}"
            )
            if not ok:
                failures.append(
                    f"IMBL vertex point: expected within_imbl_buffer=True/UNSAFE, "
                    f"got {result} / {verdict.verdict}"
                )

    print("\n" + _rule("="))
    if failures:
        print(f"RESULT: {len(failures)} failure(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
