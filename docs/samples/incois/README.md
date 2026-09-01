# INCOIS sample responses — captured 2026-09-01

Evidence for issue #3 (P3 Sprint 0 exit criterion: "Sample INCOIS PFZ response
captured"). Regenerate with `scripts/p3_sample_calls.sh` (no credentials needed).

| File | What it is |
|---|---|
| `geoserver_wms_layers_and_time.txt` | Layer inventory of INCOIS GeoServer workspace `PFZ-TUNA-SST-CHL` (`sst`, `chl` rasters; no vector/WFS layers) |
| `getfeatureinfo_sst_<loc>.json` | SST at Kochi / Chennai / Kollam via WMS `GetFeatureInfo` (`INFO_FORMAT=application/json`) |
| `getfeatureinfo_chl_<loc>.json` | Chlorophyll at the same 3 points, same mechanism |
| `erddap_dataset_list.json` | Full dataset list from `erddap.incois.gov.in` (17 datasets) |
| `pfz_advisory_text_probe.txt` | Negative result — the per-sector PFZ *text* advisory is not a plain GET |

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

## Key takeaways (full analysis: `docs/p3-data-source-spike.md`)

- **No INCOIS REST API, no API key.** `INCOIS_BASE_URL` is just a fetch base.
- **SST + chlorophyll (FR-OCEAN-2): reachable key-free** via GeoServer WMS
  `GetFeatureInfo` JSON. Usable directly from `INCOISAdapter.fetch()`.
- **PFZ centroids / advisory geometry (FR-OCEAN-1, FR-OCEAN-3): NOT solved.**
  No WFS vector layer; the text advisory needs a form POST/date param or an
  email to INCOIS user services. Open SRS §6.4 item.
- **ERDDAP** is a clean JSON/CSV griddap API and a good SST/chl fallback, but
  several datasets are archival (QuikSCAT / Oceansat-2) — verify recency.
