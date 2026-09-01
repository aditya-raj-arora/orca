#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# p3_sample_calls.sh — reproducible "one successful sample call" evidence for
# issue #3 (P3 Sprint 0). Owner: P3 (Weather & Ocean Data Engineer).
#
# Requirement(s): FR-WX-1..4, FR-OCEAN-1..4.  Design ref: LLD §2.9.
#
# Writes raw responses into docs/samples/. The INCOIS calls, the Open-Meteo
# calls and the GDACS cyclone feed need NO credentials. Only the WeatherAPI
# alert call needs a free key from weatherapi.com (email signup, no card):
#
#     export WEATHERAPI_KEY=xxxxxxxxxxxxxxxxxxxx
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
if curl -fsS -m 60 \
  "https://incois.gov.in/geoserver/PFZ-TUNA-SST-CHL/wms?service=WMS&version=1.3.0&request=GetCapabilities" \
  -o "$_caps" \
  -w "HTTP %{http_code}  %{size_download} bytes (summarised -> geoserver_wms_layers_and_time.txt)\n"; then
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
else
  echo "  GetCapabilities FAILED (non-blocking)"
fi
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
    -w "HTTP %{http_code}  ${layer} @ ${label} -> getfeatureinfo_${layer}_${label}.json\n" \
    || echo "  ${layer} @ ${label} FAILED (non-blocking)"
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
  -w "HTTP %{http_code}  %{size_download} bytes -> erddap_dataset_list.json\n" \
  || echo "  ERDDAP FAILED (non-blocking)"

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
    -w "HTTP %{http_code}  forecast @ $3 -> openmeteo_forecast_$3.json\n" \
    || echo "  forecast @ $3 FAILED (non-blocking)"
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
    -w "HTTP %{http_code}  marine @ $3 -> openmeteo_marine_$3.json\n" \
    || echo "  marine @ $3 FAILED (non-blocking)"
}
say "Wave height — Open-Meteo Marine (no key)"
om_marine "$KOCHI_LAT"   "$KOCHI_LON"   kochi
om_marine "$CHENNAI_LAT" "$CHENNAI_LON" chennai
om_marine "$KOLLAM_LAT"  "$KOLLAM_LON"  kollam

# ---------------------------------------------------------------------------
# 6. Active alerts — FR-WX-2. Two sources, both lat/lon (no district-id map):
#    a) WeatherAPI.com forecast.json?...&alerts=yes -> govt severe-weather /
#       cyclone alerts. Free key from weatherapi.com (email, no card).
#    b) GDACS GeoRSS -> tropical-cyclone events (North Indian Ocean incl.).
#       Keyless. Adapter filters <gdacs:eventtype>TC</gdacs:eventtype> and
#       distance from the query point.
# ---------------------------------------------------------------------------
WEATHERAPI_BASE="${WEATHERAPI_BASE_URL:-https://api.weatherapi.com/v1}"
GDACS_BASE="${GDACS_BASE_URL:-https://www.gdacs.org/xml}"

say "Active alerts (a) — WeatherAPI.com alerts=yes"
if [ -n "${WEATHERAPI_KEY:-}" ]; then
  wapi_alerts() { # $1=lat $2=lon $3=label
    local raw; raw="$(mktemp)"
    if curl -fsS -m 30 \
        "${WEATHERAPI_BASE}/forecast.json?key=${WEATHERAPI_KEY}&q=$1,$2&days=3&alerts=yes&aqi=no" \
        -o "$raw" \
        -w "HTTP %{http_code}  alerts @ $3 -> weatherapi_alerts_$3.json\n"; then
      # keep only location + current + alerts; drop the bulky 3-day forecast block
      python3 -c "import json,sys;d=json.load(open(sys.argv[1]));json.dump({k:d[k] for k in ('location','current','alerts') if k in d},open(sys.argv[2],'w'),indent=2)" \
        "$raw" "$OUT_WX/weatherapi_alerts_$3.json"
    else
      echo "  alerts @ $3 FAILED (non-blocking)"
    fi
    rm -f "$raw"
  }
  wapi_alerts "$KOCHI_LAT"   "$KOCHI_LON"   kochi
  wapi_alerts "$CHENNAI_LAT" "$CHENNAI_LON" chennai
  wapi_alerts "$KOLLAM_LAT"  "$KOLLAM_LON"  kollam
  echo "review docs/samples/weather/weatherapi_alerts_*.json — note rate limits + the .alerts.alert[] shape in spike §2"
else
  echo "SKIPPED — get a free key at https://www.weatherapi.com/signup.aspx (no card), then:"
  echo "          WEATHERAPI_KEY=... scripts/p3_sample_calls.sh"
fi

say "Active alerts (b) — GDACS tropical-cyclone GeoRSS (no key)"
_gdacs="$(mktemp)"
if curl -fsS -m 30 -A "Mozilla/5.0" "${GDACS_BASE}/rss.xml" -o "$_gdacs" \
     -w "HTTP %{http_code}  %{size_download} bytes (trimmed to TC items -> gdacs_tc_rss.xml)\n"; then
  python3 - "$_gdacs" "$OUT_WX/gdacs_tc_rss.xml" <<'PY'
import re, sys
x = open(sys.argv[1], encoding="utf-8", errors="replace").read()
head = x.split("<item>", 1)[0]
items = ["<item>" + b for b in re.findall(r"<item>(.*?)</item>", x, re.S)
         if "<gdacs:eventtype>TC</gdacs:eventtype>" in ("<item>" + b)]
out = head + "".join(i + "</item>\n" for i in items) + "  </channel>\n</rss>\n"
open(sys.argv[2], "w").write(out)
print(f"  {len(items)} TC item(s) kept")
PY
else
  echo "  GDACS FAILED (non-blocking)"
fi
rm -f "$_gdacs"

say "done — review docs/samples/, then update docs/CREDENTIALS.md #3/#4 status"
