#!/usr/bin/env python3
"""
rebuild_imbl_boundaries.py — replace the IMBL features in the merged geofence
dataset with India's actual bilateral maritime boundary lines.

Owner: P4 (Geospatial & Risk Engineer). Issue #99 (defect 1), FR-GEO-1/2.

WHY THIS EXISTS
---------------
`data/gis/imbl_mpa_boundaries.geojson` originally carried the India EEZ
(200 NM) MultiPolygon as an IMBL proxy (docs/p4-data-source-spike.md §2).
`GeofencingAgent._check_imbl` measures distance to `geom.boundary`, which for
a polygon is *every* ring — including the landward/coastal one. The shoreline
was therefore treated as a maritime boundary, so every coastal port came back
inside the 5 km IMBL buffer (Kochi 0.80 km, Chennai 2.10 km, Kollam 1.03 km)
while a point genuinely near the India-Sri Lanka line did not (24.5 km).
Because FR-GEO-4 makes a violation non-negotiable, that meant UNSAFE for every
query from a real fishing port.

`data/gis/imbl_india.geojson` — pulled in the same Marine Regions (VLIZ) run,
and already what `app/db/load_boundaries.py` loads for IMBL — holds the real
thing: 24 LineString features for the negotiated boundaries (Sri Lanka-India,
Bangladesh-India, Pakistan-India, Maldives-India, and the Andaman & Nicobar
lines with Indonesia, Thailand and Myanmar). Those are lines, so there is no
land ring to be confused for a boundary, and no seaward/landward disambiguation
to get wrong.

MPA features are copied through untouched: their names were cleaned up by hand
when the merged file was first built (`mpa_india.geojson` still has the raw
WDPA names, e.g. "Parc national des Sundarbans"), and regenerating them from
source would silently undo that.

Run:

    cd src/backend
    python ../../scripts/rebuild_imbl_boundaries.py

Idempotent — re-running produces the same output.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "src" / "backend"
GIS_DIR = BACKEND_DIR / "data" / "gis"

IMBL_SOURCE = GIS_DIR / "imbl_india.geojson"
MERGED = GIS_DIR / "imbl_mpa_boundaries.geojson"

# geofence_boundary.name / .source are VARCHAR(100) (app/db/schema.sql §3) —
# anything longer would fail to seed.
MAX_FIELD_LEN = 100
SOURCE_LABEL = "Marine Regions (VLIZ) Maritime Boundaries v12"


def _imbl_features() -> list[dict]:
    raw = json.loads(IMBL_SOURCE.read_text(encoding="utf-8"))
    features = []
    for feature in raw["features"]:
        props = feature.get("properties", {})
        name = (props.get("name") or "India maritime boundary")[:MAX_FIELD_LEN]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "type": "IMBL",
                    "name": name,
                    "source": SOURCE_LABEL,
                    # Kept from the source data: distinguishes a negotiated
                    # treaty line from a "connection line" joining two of them.
                    "line_type": props.get("line_type"),
                },
                "geometry": feature["geometry"],
            }
        )
    return features


def main() -> int:
    merged = json.loads(MERGED.read_text(encoding="utf-8"))
    mpa = [f for f in merged["features"] if f["properties"].get("type") == "MPA"]
    if not mpa:
        print(f"{MERGED} has no MPA features — refusing to write", file=sys.stderr)
        return 1

    imbl = _imbl_features()
    if not imbl:
        print(f"{IMBL_SOURCE} yielded no IMBL features — refusing to write", file=sys.stderr)
        return 1

    for feature in imbl:
        geom_type = feature["geometry"]["type"]
        if geom_type not in ("LineString", "MultiLineString"):
            print(
                f"IMBL feature {feature['properties']['name']!r} is a {geom_type}, "
                "not a line — that is the defect this script exists to fix",
                file=sys.stderr,
            )
            return 1

    output = {
        "type": "FeatureCollection",
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": "scripts/rebuild_imbl_boundaries.py (issue #99)",
        "features": imbl + mpa,
    }
    MERGED.write_text(json.dumps(output), encoding="utf-8")
    print(f"Wrote {MERGED}: {len(imbl)} IMBL line features + {len(mpa)} MPA polygons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
