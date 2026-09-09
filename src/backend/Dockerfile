# Prototype single-app deployment per HLD §7.
# TODO(P6 - QA/Integration Lead): review before Day 6 hardening — pin the
# Python patch version, add a non-root user, and a HEALTHCHECK against
# /healthz once main.py's endpoints are implemented.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Runtime data files, NOT optional (#107):
#   data/gis/       — the IMBL/MPA boundary GeoJSON GISBoundaryAdapter loads
#                     (GIS_BOUNDARY_DATA_PATH). Without it GeofencingAgent
#                     reports unavailable, which correctly forces
#                     INSUFFICIENT_DATA on EVERY query (FR-RISK-3) — i.e. the
#                     deployed app cannot answer anything at all.
#   data/snapshots/ — the bundled PFZ snapshot INCOISAdapter serves as 'stale'
#                     when the live WFS is down (#38 / HLD §9 RISK-1). Missing
#                     here means the demo-resilience fallback silently isn't
#                     there on the one day it matters.
# These were omitted when only `app/` was copied, so both failures only ever
# showed up in the deployed image and never locally.
COPY data ./data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
