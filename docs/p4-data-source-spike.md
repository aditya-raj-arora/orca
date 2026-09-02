# P4 Data-Source Access Spike — IMBL + MPA Boundaries

Closes the investigation half of **issue #4** ("[P4] Confirm/download
IMBL+MPA GIS data; set up PostgreSQL+PostGIS", SRS RISK-4).

- **Requirement(s):** FR-GEO-1..4, FR-RISK-1..3
- **Design ref:** LLD §2.5, §2.6, §3 (`geofence_boundary` table)
- **Owner:** P4 (Mukta Motwani)
- **Date:** 2026-09-02
- **Status:** unblocked — dataset downloaded, converted, seeded, and verified
  against a local PostGIS instance.

---

## 1. Summary

| Need | Source decided | Key? | Confidence |
|---|---|---|---|
| IMBL (maritime boundary line), FR-GEO-1/2 | **Marine Regions (VLIZ)** Maritime Boundaries Geodatabase v12 — Indian EEZ (200 NM), MRGID 8480, via keyless WFS GeoJSON | none | high — verified live, area matches known Indian EEZ size (~1.66M km²) |
| MPA boundaries, FR-GEO-1/3 | **WDPA / Protected Planet** monthly country package (bulk download, no API token) for India, filtered to `REALM` in `{Marine, Coastal}` | none | high — verified, 6 real marine/coastal protected areas extracted |

Net: **both parts of RISK-4 are resolved.** No account, API token, or paid
plan was needed for either source — see §4 on why the token-gated Protected
Planet *API* was dropped in favor of its keyless bulk download.

---

## 2. IMBL — Marine Regions (VLIZ)

`docs/CREDENTIALS.md` #5 named Marine Regions as a candidate aggregator for
India's maritime boundary. Confirmed:

- Keyless WFS `GetFeature` against `geo.vliz.be`, filtered to MRGID 8480
  (`Indian Exclusive Economic Zone`, `pol_type=200NM`):

  ```
  https://geo.vliz.be/geoserver/MarineRegions/wfs?service=WFS&version=1.0.0&request=GetFeature&typeName=MarineRegions:eez&outputFormat=json&cql_filter=mrgid=8480
  ```

- Returns one `MultiPolygon` GeoJSON feature, WGS84 (EPSG:4326), `area_km2`
  1,659,500 — matches the publicly cited Indian EEZ area, so the geometry is
  the real boundary, not a placeholder.
- The true India-specific "IMBL" (the bilaterally negotiated line, distinct
  from the 200 NM EEZ limit) isn't published as an open geometry anywhere the
  team could find without a government data request; the EEZ limit is the
  open-data proxy this project uses for the geofence boundary, consistent
  with the "aggregator sites... has EEZ/boundary layers" framing in
  `docs/CREDENTIALS.md` #5. Flagging this distinction explicitly rather than
  letting it read as the literal negotiated IMBL.

## 3. MPA — WDPA / Protected Planet

`docs/CREDENTIALS.md` #5 named Protected Planet as the aggregator to check.
Its REST API (`api.protectedplanet.net`) returned `401 Unauthorized` on every
route tested — it requires a free account + API token, and creating accounts
on the team's behalf is out of scope for an automated data pull.

Its **bulk country download** needs no token, though — Protected Planet
publishes a monthly WDPA_WDOECM release per ISO3 country code as a File
Geodatabase zip via CloudFront:

```
https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_<Mon><Year>_Public_<ISO3>.zip
```

For India (`IND`), the September 2026 release contains 63 protected-area
polygons. Filtered to `REALM in {"Marine", "Coastal"}` (dropping purely
terrestrial parks like Kaziranga), 6 real MPAs remain:

| Name | Designation | Marine area (km²) |
|---|---|---|
| Sundarbans National Park | World Heritage Site | 241.6 |
| Chilika Lake | Ramsar Site | 738.6 |
| Sundarban Wetland | Ramsar Site | 2055.2 |
| Gulf of Mannar Marine Biosphere Reserve | Ramsar Site | 519.9 |
| Pichavaram Mangrove | Ramsar Site | 4.1 |
| Thane Creek | Ramsar Site | 19.1 |

Read via Python's `fiona` (bundles a GDAL build — no system GDAL/`ogr2ogr`
install needed) since the release ships as an Esri File Geodatabase, not a
shapefile.

## 4. Combined dataset

Both sources normalise cleanly to one FeatureCollection: WGS84 (EPSG:4326),
`properties.type` in `{"IMBL","MPA"}` matching the `geofence_boundary.type`
CHECK constraint, `properties.name`/`properties.source` both kept under 100
chars to fit that table's `VARCHAR(100)` columns. Written to
`src/backend/data/gis/imbl_mpa_boundaries.geojson` (the default
`GIS_BOUNDARY_DATA_PATH`), 7 features, ~2 MB.

`GISBoundaryAdapter.fetch()` (`app/data_access/gis_boundary_adapter.py`)
loads and caches this file in-process, same error contract as the other
adapters (`status="unavailable"` rather than raising).

## 4a. Follow-up: closing the "6 of ~900" gap (PR #62 review, Mukta)

Protected Planet's own India country profile cites ~900 WDPA-registered
protected areas nationally; the bulk country package only ships what's
publicly releasable — **India restricts public geometry for most of its
registered protected areas**, a country-level WDPA policy, not an artifact of
using the bulk download over the API (the API enforces the same restriction
and additionally needs a token). Public total is 90 (63 polygons + 27
points); 6 of those 63 polygons are tagged marine/coastal, the other 84 are
inland and irrelevant to this geofence.

Tried to close the gap with an India-native source that might not be subject
to WDPA's global restriction:

- **WII/ENVIS geographic-data server, India Biodiversity Portal GeoServer,
  ISRO Bhuvan's OWS subdomains** (`docs/CREDENTIALS.md` #5 candidates): all
  connection-timed-out from this environment. Bhuvan's main portal
  (`bhuvan.nrsc.gov.in`) resolves, but every `bhuvan-vec*`/`bhuvan-wms*` OWS
  host tried does not — likely India-network-gated rather than actually down.
- **OpenStreetMap (Overpass API)**: does have some Indian marine protected
  areas WDPA doesn't publish — found a `boundary=protected_area` relation
  named "Marine National Park" near the real Gulf of Kutch Marine National
  Park (OSM relation 21253238) plus two Andaman island sanctuaries (Cinque
  Islands WLS, Snake Island II WLS). But the Gulf of Kutch relation resolves
  to a single ~8.9 km² fragment (one reef/island), not the park's full
  ~458 km² multi-island extent — OSM's mapping here is real but partial, and
  stitching a trustworthy boundary from it would need assembling many
  individual island/reef relations plus manual verification against an
  authoritative source. Given FR-GEO-4 (a geofence result must never be
  softened), seeding a boundary that's silently 98% smaller than the actual
  park is worse than not having it — a vessel well inside the real park could
  read as clear. Not added to the seeded dataset on that basis.

**Net for now**: the 6 WDPA marine/coastal MPAs stand as the seeded set;
closing further requires either an India-network vantage point to reach the
three gated government services above, or a manual multi-relation OSM
assembly + verification pass — flagging as a Sprint 1+ follow-up rather than
blocking Sprint 0 exit criteria (which only requires ≥1 MPA seeded).

## 5. Verified against local PostGIS

Confirmed against `postgis/postgis:16-3.4` via `docker compose up db`:

- `schema.sql` applies cleanly (`CREATE EXTENSION`/`CREATE TABLE`/`CREATE
  INDEX`, no errors) — see acceptance criterion 2.
- `scripts/seed_geofence_boundaries.py` loads the GeoJSON above and inserts
  it into `geofence_boundary` via `ST_GeomFromGeoJSON` — 1 IMBL + 6 MPA rows,
  `ST_GeometryType` = `ST_MultiPolygon` on every row, IMBL row's
  `ST_Area(geometry::geography)` ≈ 1,659,500 km² (matches §2). Re-running the
  script is idempotent (`TRUNCATE` + re-insert, same row count).

## 6. Reproduce

```bash
docker compose up -d db      # applies schema.sql automatically
cd src/backend
python ../../scripts/seed_geofence_boundaries.py
```

Regenerating the source GeoJSON itself (if the upstream data needs a
refresh) isn't scripted yet — it was a one-off `fiona`/`shapely` pull done
for this spike. Worth a follow-up script if the team wants the dataset
refreshed later in the project (not blocking — this is reference data per
HLD §4.1, not something expected to change during the prototype window).
