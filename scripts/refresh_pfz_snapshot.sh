#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# refresh_pfz_snapshot.sh — refresh the bundled INCOIS PFZ advisory snapshot
# used as the demo-resilience fallback (#38 / HLD §9 RISK-1).
#
# INCOISAdapter serves this file (gzipped GeoJSON) as status='stale' when the
# live WFS is unreachable and the per-day cache is cold. Run this daily-ish
# during the sprint and commit the result so the demo has a recent fallback.
#
# Owner: P3.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/src/backend/data/snapshots/pfz_latest.json.gz"
URL="https://incois.gov.in/geoserver/PFZ_Automation/ows?service=WFS&version=1.1.0&request=GetFeature&typeName=PFZ_Automation:pfzlines&outputFormat=application/json"

mkdir -p "$(dirname "$OUT")"
tmp="$(mktemp)"
curl -fsS -m 90 "$URL" -o "$tmp" -w "live WFS: HTTP %{http_code}  %{size_download} bytes\n"

python3 - "$tmp" "$OUT" <<'PY'
import gzip, json, sys
gj = json.load(open(sys.argv[1]))
feats = gj.get("features") or []
if not feats:
    sys.exit("refusing to write an empty snapshot")

def _round(x):  # 5 dp ~ 1 m: ample for a fishing advisory, ~halves the gz size
    if isinstance(x, list):
        return [_round(v) for v in x]
    return round(x, 5) if isinstance(x, float) else x

for ft in feats:
    g = ft.get("geometry") or {}
    if "coordinates" in g:
        g["coordinates"] = _round(g["coordinates"])

props = feats[0].get("properties") or {}
with gzip.open(sys.argv[2], "wt", encoding="utf-8") as f:
    json.dump({"type": "FeatureCollection", "features": feats,
               "totalFeatures": gj.get("totalFeatures"), "crs": gj.get("crs")},
              f, separators=(",", ":"))
print(f"wrote {sys.argv[2]}: {len(feats)} features, advisory Year={props.get('Year')} "
      f"Julian_day={props.get('Julian_day')}")
PY

ls -l "$OUT"
