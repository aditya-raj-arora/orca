# INCOIS sample responses — captured 2026-09-01

Evidence for issue #3 (P3 Sprint 0 exit criterion: "Sample INCOIS PFZ response
captured"). Regenerate with `scripts/p3_sample_calls.sh` (no credentials needed).

| File | What it is |
|---|---|
| `pfz_wfs_pfzlines_sample.json` | **PFZ advisory geometry** — first 3 of 96 features from the `PFZ_Automation:pfzlines` WFS→GeoJSON feed (FR-OCEAN-1/3) |
| `geoserver_wms_layers_and_time.txt` | Layer inventory of INCOIS GeoServer workspace `PFZ-TUNA-SST-CHL` (`sst`, `chl` rasters) |
| `getfeatureinfo_sst_<loc>.json` | SST at Kochi / Chennai / Kollam via WMS `GetFeatureInfo` (`INFO_FORMAT=application/json`) |
| `getfeatureinfo_chl_<loc>.json` | Chlorophyll at the same 3 points, same mechanism |
| `erddap_dataset_list.json` | Full dataset list from `erddap.incois.gov.in` (17 datasets) |

## What the samples actually returned (2026-09-01)

| Point | SST `GRAY_INDEX` | CHL `GRAY_INDEX` |
|---|---|---|
| Kochi (9.93, 76.26) | `32.45` (°C) | `null` |
| Kollam (8.88, 76.60) | `30.17` (°C) | `null` |
| Chennai (13.08, 80.27) | `-1` &nbsp;← no-data sentinel | `null` |

So this path **works and returns real numbers**, but two failure modes are
already visible and the adapter MUST handle both without fabricating:

- **`GRAY_INDEX = -1`** (and possibly large negatives) = "no data at this
  pixel/date" → map to `None` (FR-OCEAN-4 / NFR-REL-1).
- **`GRAY_INDEX = null`** = layer had nothing to sample (chl came back empty at
  all 3 coastal points on this date — may need a `TIME=` param for the latest
  published mosaic, or chl coverage is just sparse near the coast). Treat as
  `None`, and flag chlorophyll-via-WMS as **not yet reliable** — see the spike
  doc.

## PFZ advisory geometry (FR-OCEAN-1 / FR-OCEAN-3)

```
GET https://incois.gov.in/geoserver/PFZ_Automation/ows
    ?service=WFS&version=1.1.0&request=GetFeature
    &typeName=PFZ_Automation:pfzlines&outputFormat=application/json
```

Keyless GeoJSON. 2026-09-01: `FeatureCollection`, 96 `MultiLineString`
features (advisory boundary lines), coords `[lon,lat]` WGS84, ~1.3 MB.
Per-feature `Year`+`Julian_day` (`2026`+`243` → 2026-08-31) gives the advisory
date for `is_stale` (FR-OCEAN-4). Adapter: fetch once/day + cache, then
haversine the query point to the nearest line. Found via the PfzWebGis app's
network calls — see `docs/p3-data-source-spike.md` §3.3.

## Key takeaways (full analysis: `docs/p3-data-source-spike.md`)

- **No INCOIS REST API, no API key.** Everything is open GeoServer OGC services.
- **SST + chlorophyll (FR-OCEAN-2): key-free** via GeoServer WMS
  `GetFeatureInfo` JSON. Watch `GRAY_INDEX` = `-1` / `null` → `None`, never a
  fabricated value. Chlorophyll came back empty at all 3 points — treat as
  best-effort / often-`None`.
- **PFZ advisory geometry (FR-OCEAN-1/3): key-free** via GeoServer WFS
  `PFZ_Automation:pfzlines` GeoJSON (see above).
- **ERDDAP** is a clean JSON/CSV griddap API and a good SST/chl fallback, but
  several datasets are archival (QuikSCAT / Oceansat-2) — verify recency.
