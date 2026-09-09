# P4 — Final DB + geofence sanity check (#45)

Owner: P4. Requirements: FR-GEO-1..4, FR-RISK-1..3. LLD §3, §2.5.

The repeatable check is `scripts/geofence_risk_sanity_check.py` (no LLM key,
no DB — the real bundled GeoJSON only, through `GeofencingAgent`/
`RiskSafetyAgent` directly). Run from `src/backend`:

```
cd src/backend && python ../../scripts/geofence_risk_sanity_check.py
```

## Architecture finding — read this before the acceptance criteria below

**`GeofencingAgent` never reads the Postgres `geofence_boundary` table.**
`GISBoundaryAdapter.fetch()` (`app/data_access/gis_boundary_adapter.py`) loads
exclusively from the static GeoJSON at `GIS_BOUNDARY_DATA_PATH`
(`./data/gis/imbl_mpa_boundaries.geojson`, shipped in the Docker image —
confirmed via `Dockerfile`'s `COPY data ./data` and that the file is
git-tracked, not `.gitignore`d).

The DB side exists — `app/db/schema.sql` defines the `geofence_boundary`
table with a PostGIS `GEOMETRY(Geometry, 4326)` column and a GIST index, and
`app/db/load_boundaries.py` is a one-off seeder script for it — but nothing in
the live request path (`GeofencingAgent.check()` / `.list_nearby()`) queries
it. `app/db/models.py`'s `GeofenceBoundary` SQLAlchemy model even carries an
open `TODO(P4)` noting the geometry column isn't wired to GeoAlchemy2 yet.

**Consequence for this issue's original acceptance criteria:** "PostGIS
enabled and `geofence_boundary` seeded in prod" does not currently gate
demo-day correctness — the file-based path is what every query actually runs
through, DB state notwithstanding. What *does* gate correctness is the
GeoJSON file itself: present, valid, and containing the right features, which
is what the checks below verify instead. Whether the DB path should be wired
up for real (matching the original LLD §3 design) is a real follow-up, not
something to silently paper over — flagging it here rather than closing this
out as if the stated AC were met as originally written.

## Revised acceptance criteria (what actually gates demo correctness)

### 1. Bundled GeoJSON holds IMBL + demo-relevant MPAs

`data/gis/imbl_mpa_boundaries.geojson`: 30 features — 24 IMBL segments, 6 MPAs
(Sundarbans National Park, Chilika Lake, Sundarban Wetland, Gulf of Mannar
Marine Biosphere Reserve, Pichavaram Mangrove, Thane Creek). Gulf of Mannar is
the demo-relevant one (used as the known-unsafe MPA case below).

### 2. Geofencing returns correct in/near/clear for the demo locations

`geofence_risk_sanity_check.py`, local run (2026-09-09):

| Location | within_mpa | within_imbl_buffer | IMBL distance | Verdict |
|---|---|---|---|---|
| Kochi (9.93, 76.26) | False | False | 272.4 km | SAFE |
| Chennai (13.08, 80.27) | False | False | 277.4 km | SAFE |
| Kollam (8.88, 76.60) | False | False | 215.5 km | SAFE |

All three demo locations are clear of every boundary, as expected — they're
coastal fishing-ground queries, not offshore boundary-crossing ones.

### 3. Risk verdict correct for a known-safe and a known-unsafe location

- **Known-unsafe (inside MPA):** a representative interior point of Gulf of
  Mannar Marine Biosphere Reserve (9.1861, 78.8919) → `within_mpa=True`,
  verdict `UNSAFE` — with deliberately calm, fully-available synthetic
  weather, so the verdict is attributable only to the geofence violation
  (FR-GEO-4: non-negotiable, cannot be softened by favourable weather).
- **Known-unsafe (IMBL buffer):** a vertex read directly off one of the
  bundled IMBL LineString segments (6.6417, 94.6333 — Andaman side, not near
  any demo location, which is fine; this case tests the buffer logic, not a
  demo query) → `within_imbl_buffer=True`, distance 0.00 km, verdict `UNSAFE`.
- **Known-safe:** all three demo locations above already double as the
  known-safe case — clear geofence + calm weather → `SAFE`.

All five cases pass. `scripts/geofence_risk_sanity_check.py` exits 0.

## Still open (not covered by this check)

- **PostGIS-on-prod / `geofence_boundary` seeded** — per the finding above,
  not load-bearing for the demo as currently wired. If the team wants the DB
  path to actually work (matching the original LLD §3 design, e.g. for a
  future admin UI to edit boundaries without a redeploy), that's a real
  follow-up: wire `GeoAlchemy2`'s `Geometry` type into `GeofenceBoundary`
  (the existing `TODO(P4)`), and either point `GISBoundaryAdapter` at the DB
  or accept the file-based design going forward and remove the dead
  `load_boundaries.py` / table so the LLD doesn't overstate what's real.
- **Confirming the file loads from the *deployed* Render instance** — this
  check itself ran locally only. Indirect confirmation exists: a live query
  through the deployed frontend (`orca-frontend-krth`, 2026-09-09, during
  #36/#42's rehearsal) for Kochi returned "The location is not within an IMBL
  buffer or MPA... 273.0 km from IMBL" — matching this script's local 272.4 km
  figure closely enough to confirm the same file is loading in the container
  (the small delta is `_haversine_km` on slightly different resolved
  coordinates, not a different dataset). Worth re-running this script itself
  via the Render shell for a direct match, but not currently believed broken.
