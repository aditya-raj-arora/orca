#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# p3_sample_calls.sh — reproducible "one successful sample call" evidence for
# issue #3 (P3 Sprint 0). Owner: P3 (Weather & Ocean Data Engineer).
#
# Requirement(s): FR-WX-1..4, FR-OCEAN-1..4.  Design ref: LLD §2.9.
#
# Writes raw responses into docs/samples/. The INCOIS calls need no
# credentials. The weather call needs a free OpenWeather key:
#
#     export WEATHER_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
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
# 1. INCOIS — GeoServer WMS GetCapabilities (layer inventory), no key
# ---------------------------------------------------------------------------
say "INCOIS GeoServer WMS GetCapabilities"
curl -fsS -m 60 \
  "https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms?service=WMS&version=1.3.0&request=GetCapabilities" \
  -o "$OUT_INCOIS/geoserver_wms_getcapabilities.xml" \
  -w "HTTP %{http_code}  %{size_download} bytes -> geoserver_wms_getcapabilities.xml\n"

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
# 4. Weather provider — OpenWeather One Call API 3.0 (needs WEATHER_API_KEY)
#    Gives wind / precipitation / visibility / active alerts.
#    Does NOT give wave height -> see docs/p3-data-source-spike.md section 2.
# ---------------------------------------------------------------------------
say "Weather provider — OpenWeather One Call 3.0"
if [ -n "${WEATHER_API_KEY:-}" ]; then
  BASE="${WEATHER_API_BASE_URL:-https://api.openweathermap.org/data/3.0}"
  curl -fsS -m 30 \
    "${BASE}/onecall?lat=${KOCHI_LAT}&lon=${KOCHI_LON}&units=metric&exclude=minutely&appid=${WEATHER_API_KEY}" \
    -o "$OUT_WX/openweather_onecall_kochi.json" \
    -w "HTTP %{http_code}  %{size_download} bytes -> openweather_onecall_kochi.json\n"
  echo "captured — commit docs/samples/weather/openweather_onecall_kochi.json"
else
  echo "SKIPPED — set WEATHER_API_KEY first (free key: https://openweathermap.org/api)."
fi

# ---------------------------------------------------------------------------
# 5. (Optional) Open-Meteo Marine API — free, NO key, HAS wave height.
#    Candidate for the wave-height half of FR-WX-1. Kept here so the team can
#    compare it against OpenWeather when deciding the provider.
# ---------------------------------------------------------------------------
say "Open-Meteo Marine API (no key) — wave height candidate"
curl -fsS -m 30 \
  "https://marine-api.open-meteo.com/v1/marine?latitude=${KOCHI_LAT}&longitude=${KOCHI_LON}&current=wave_height,wave_direction,wave_period&hourly=wave_height" \
  -o "$OUT_WX/openmeteo_marine_kochi.json" \
  -w "HTTP %{http_code}  %{size_download} bytes -> openmeteo_marine_kochi.json\n" || \
  echo "open-meteo call failed (non-blocking)"

say "done — review docs/samples/, then update docs/CREDENTIALS.md #3/#4 status"
