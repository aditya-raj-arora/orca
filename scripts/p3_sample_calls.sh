#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# p3_sample_calls.sh — reproducible "one successful sample call" evidence for
# issue #3 (P3 Sprint 0). Owner: P3 (Weather & Ocean Data Engineer).
#
# Requirement(s): FR-WX-1..4, FR-OCEAN-1..4.  Design ref: LLD §2.9.
#
# Writes raw responses into docs/samples/. The INCOIS calls and the Open-Meteo
# calls need NO credentials. Only the IMD alert call needs a free key from
# api.imd.gov.in/register.php (no card):
#
#     export IMD_API_KEY=xxxxxxxxxxxxxxxxxxxx
#     scripts/p3_sample_calls.sh
#
# Test locations (used across the P3 work per issue #10 / #15 acceptance
# criteria): Kochi, Chennai, Kollam.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_INCOIS="$ROOT/docs/samples/incois"
OUT_WX="$ROOT/docs/samples/weather"
mkdir -p "$OUT_INCOIS" "$OUT_WX"

# lat/lon for the three reference locations
KOCHI_LAT=9.93;   KOCHI_LON=76.26
CHENNAI_LAT=13.08; CHENNAI_LON=80.27
KOLLAM_LAT=8.88;   KOLLAM_LON=76.60

say() { printf '\n=== %s ===\n' "$1"; }

# ---------------------------------------------------------------------------
# 1. INCOIS — GeoServer WMS GetCapabilities (layer inventory), no key.
#    The full caps doc is ~190 KB; we keep only a small layer/time summary.
# ---------------------------------------------------------------------------
say "INCOIS GeoServer WMS GetCapabilities"
_caps="$(mktemp)"
curl -fsS -m 60 \
  "https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms?service=WMS&version=1.3.0&request=GetCapabilities" \
  -o "$_caps" \
  -w "HTTP %{http_code}  %{size_download} bytes (summarised -> geoserver_wms_layers_and_time.txt)\n"
python3 - "$_caps" "$OUT_INCOIS/geoserver_wms_layers_and_time.txt" <<'PY'
import re, sys
x = open(sys.argv[1], encoding="utf-8", errors="replace").read()
names = re.findall(r"<Name>([^<]+)</Name>", x)
tdim  = re.findall(r'<Dimension name="time"[^>]*>([^<]+)</Dimension>', x)
out  = "INCOIS GeoServer WMS - workspace PFZ-TUNA-SST-CHL\n"
out += "Endpoint : https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms\n"
out += "Also WFS : https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wfs  (0 vector feature types - rasters only)\n\n"
out += "Layer <Name> values:\n" + "".join(f"  - {n}\n" for n in names)
out += "\ntime dimension extents:\n" + ("".join(f"  {t}\n" for t in tdim) if tdim else "  (none parsed - query GetCapabilities directly for TIME=)\n")
open(sys.argv[2], "w").write(out)
PY
rm -f "$_caps"

# ---------------------------------------------------------------------------
# 2. INCOIS — SST + chlorophyll at a point via WMS GetFeatureInfo (JSON), no key
#    This is the candidate path for INCOISAdapter.fetch() FR-OCEAN-2.
#    NOTE: GRAY_INDEX = -1 (or a large negative) means "no data at this
#    point/date" -> the adapter must surface that as None, never a guess.
# ---------------------------------------------------------------------------
gfi() { # $1=layer  $2=lat  $3=lon  $4=label
  local layer=$1 lat=$2 lon=$3 label=$4
  # 0.5-degree bbox centred on the point; ask for the centre pixel of a 256px tile
  local minx miny maxx maxy
  minx=$(awk "BEGIN{print $lon-0.25}"); maxx=$(awk "BEGIN{print $lon+0.25}")
  miny=$(awk "BEGIN{print $lat-0.25}"); maxy=$(awk "BEGIN{print $lat+0.25}")
  curl -fsS -m 40 \
    "https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms?service=WMS&version=1.1.1&request=GetFeatureInfo&layers=${layer}&query_layers=${layer}&info_format=application/json&srs=EPSG:4326&bbox=${minx},${miny},${maxx},${maxy}&width=256&height=256&x=128&y=128" \
    -o "$OUT_INCOIS/getfeatureinfo_${layer}_${label}.json" \
    -w "HTTP %{http_code}  ${layer} @ ${label} -> getfeatureinfo_${layer}_${label}.json\n"
}
say "INCOIS WMS GetFeatureInfo — SST + chlorophyll at the 3 reference points"
gfi sst "$KOCHI_LAT"   "$KOCHI_LON"   kochi
gfi chl "$KOCHI_LAT"   "$KOCHI_LON"   kochi
gfi sst "$CHENNAI_LAT" "$CHENNAI_LON" chennai
gfi chl "$CHENNAI_LAT" "$CHENNAI_LON" chennai
gfi sst "$KOLLAM_LAT"  "$KOLLAM_LON"  kollam
gfi chl "$KOLLAM_LAT"  "$KOLLAM_LON"  kollam

# ---------------------------------------------------------------------------
# 3. INCOIS — ERDDAP dataset list (fallback data path for SST/chl)
# ---------------------------------------------------------------------------
say "INCOIS ERDDAP dataset list"
curl -fsS -m 45 \
  "https://erddap.incois.gov.in/erddap/info/index.json?page=1&itemsPerPage=1000" \
  -o "$OUT_INCOIS/erddap_dataset_list.json" \
  -w "HTTP %{http_code}  %{size_download} bytes -> erddap_dataset_list.json\n"

# ---------------------------------------------------------------------------
# 4. Weather forecast — Open-Meteo Forecast API, NO key.
#    Gives wind / gusts / direction / precipitation / visibility (FR-WX-1).
# ---------------------------------------------------------------------------
FORECAST_BASE="${WEATHER_FORECAST_BASE_URL:-https://api.open-meteo.com/v1}"
MARINE_BASE="${MARINE_API_BASE_URL:-https://marine-api.open-meteo.com/v1}"
om_fc() { # $1=lat $2=lon $3=label
  curl -fsS -m 30 \
    "${FORECAST_BASE}/forecast?latitude=$1&longitude=$2&current=wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,visibility&hourly=wind_speed_10m,precipitation,visibility&forecast_days=2&timezone=auto" \
    -o "$OUT_WX/openmeteo_forecast_$3.json" \
    -w "HTTP %{http_code}  forecast @ $3 -> openmeteo_forecast_$3.json\n"
}
say "Weather forecast — Open-Meteo (no key)"
om_fc "$KOCHI_LAT"   "$KOCHI_LON"   kochi
om_fc "$CHENNAI_LAT" "$CHENNAI_LON" chennai
om_fc "$KOLLAM_LAT"  "$KOLLAM_LON"  kollam

# ---------------------------------------------------------------------------
# 5. Wave height — Open-Meteo Marine API, NO key (FR-WX-1).
# ---------------------------------------------------------------------------
om_marine() { # $1=lat $2=lon $3=label
  curl -fsS -m 30 \
    "${MARINE_BASE}/marine?latitude=$1&longitude=$2&current=wave_height,wave_direction,wave_period&hourly=wave_height&forecast_days=2&timezone=auto" \
    -o "$OUT_WX/openmeteo_marine_$3.json" \
    -w "HTTP %{http_code}  marine @ $3 -> openmeteo_marine_$3.json\n"
}
say "Wave height — Open-Meteo Marine (no key)"
om_marine "$KOCHI_LAT"   "$KOCHI_LON"   kochi
om_marine "$CHENNAI_LAT" "$CHENNAI_LON" chennai
om_marine "$KOLLAM_LAT"  "$KOLLAM_LON"  kollam

# ---------------------------------------------------------------------------
# 6. Active alerts — IMD API (needs IMD_API_KEY from api.imd.gov.in). FR-WX-2.
#    Endpoints are keyed by district/subdivision/sea-area id, not lat/lon.
#    NOTE: key transport (header vs ?api_key=) is unconfirmed until you
#    register — this tries a query param; adjust once you have the key.
# ---------------------------------------------------------------------------
IMD_BASE="${IMD_API_BASE_URL:-https://api.imd.gov.in/api/v1}"
say "Active alerts — IMD API"
if [ -n "${IMD_API_KEY:-}" ]; then
  for ep in subdivisionwarning districtnowcast cyclone_track coastalbulletin; do
    curl -fsS -m 30 \
      "${IMD_BASE}/${ep}?api_key=${IMD_API_KEY}" \
      -o "$OUT_WX/imd_${ep}.json" \
      -w "HTTP %{http_code}  ${ep} -> imd_${ep}.json\n" || echo "  ${ep} failed"
  done
  echo "review the imd_*.json files; note in the spike doc how the key is passed."
else
  echo "SKIPPED — register at https://api.imd.gov.in/register.php (free, no card),"
  echo "          then: IMD_API_KEY=... scripts/p3_sample_calls.sh"
fi

say "done — review docs/samples/, then update docs/CREDENTIALS.md #3/#4 status"
