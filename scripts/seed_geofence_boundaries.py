#!/usr/bin/env python3
"""
seed_geofence_boundaries.py — seed the `geofence_boundary` table (app/db/
schema.sql) from the GIS_BOUNDARY_DATA_PATH GeoJSON.

Owner: P4 (Geospatial & Risk Engineer). Issue #4 acceptance criteria:
"geofence_boundary table seeded with at least IMBL + one MPA".

Run against a local PostGIS instance (schema.sql must already be applied —
`docker compose up db` does this automatically via docker-entrypoint-initdb.d):

    cd src/backend
    python ../../scripts/seed_geofence_boundaries.py

Uses asyncpg directly (raw SQL + ST_GeomFromGeoJSON) rather than the
SQLAlchemy models in app/db/models.py, because GeofenceBoundary.geometry is
still a plain column there (see the geoalchemy2 TODO on that model) — wiring
that up is issue #11/#16 scope, not this one. This script only needs
asyncpg + PostGIS's ST_GeomFromGeoJSON, both already available.

Idempotent: clears existing rows for the boundary `source`s in the input file
before inserting, so re-running after a data refresh doesn't duplicate rows.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import asyncpg

BACKEND_DIR = Path(__file__).resolve().parent.parent / "src" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings  # noqa: E402


def _asyncpg_dsn(database_url: str) -> str:
    # asyncpg.connect() wants a plain postgresql:// DSN, not SQLAlchemy's
    # postgresql+asyncpg:// driver-qualified form.
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def main() -> None:
    settings = get_settings()
    data_path = Path(settings.gis_boundary_data_path)
    if not data_path.is_absolute():
        data_path = BACKEND_DIR / data_path

    with data_path.open("r", encoding="utf-8") as f:
        geojson = json.load(f)

    features = geojson.get("features", [])
    if not features:
        raise SystemExit(f"No features found in {data_path}")

    conn = await asyncpg.connect(_asyncpg_dsn(settings.database_url))
    try:
        # This script is the sole seeder of geofence_boundary for the
        # prototype (no other writer exists yet, LLD §3), so a full replace
        # keeps re-runs idempotent without depending on a fragile natural key.
        await conn.execute("TRUNCATE TABLE geofence_boundary")

        inserted = {"IMBL": 0, "MPA": 0}
        for feature in features:
            props = feature["properties"]
            boundary_type = props.get("type")
            if boundary_type not in ("IMBL", "MPA"):
                print(f"skipping feature with unexpected type={boundary_type!r}")
                continue
            await conn.execute(
                """
                INSERT INTO geofence_boundary (type, name, geometry, source)
                VALUES ($1, $2, ST_SetSRID(ST_GeomFromGeoJSON($3), 4326), $4)
                """,
                boundary_type,
                props.get("name"),
                json.dumps(feature["geometry"]),
                props.get("source"),
            )
            inserted[boundary_type] += 1

        print(f"Seeded geofence_boundary: {inserted['IMBL']} IMBL, {inserted['MPA']} MPA")
        assert inserted["IMBL"] >= 1 and inserted["MPA"] >= 1, (
            "acceptance criteria (issue #4) requires at least one IMBL + one MPA row"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
