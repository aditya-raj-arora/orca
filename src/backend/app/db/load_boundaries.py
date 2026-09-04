"""One-off script to seed geofence_boundary from local GeoJSON files.
Run manually: python app/db/load_boundaries.py
"""
import json
import psycopg

from app.core.config import get_settings

def _sync_url(async_url: str) -> str:
    return async_url.replace("postgresql+asyncpg://", "postgresql://")

def load_geojson(path: str, boundary_type: str, source_name: str) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    db_url = _sync_url(get_settings().database_url)
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            for feature in data["features"]:
                geom_json = json.dumps(feature["geometry"])
                name = feature["properties"].get("name")
                cur.execute(
                    """
                    INSERT INTO geofence_boundary (type, name, geometry, source)
                    VALUES (%s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), %s)
                    """,
                    (boundary_type, name, geom_json, source_name),
                )
        conn.commit()
    print(f"Loaded {len(data['features'])} features from {path} as {boundary_type}")

if __name__ == "__main__":
    load_geojson("data/gis/mpa_india.geojson", "MPA", "ProtectedPlanet")
    load_geojson("data/gis/imbl_india.geojson", "IMBL", "MarineRegions")