/**
 * Normalizes the `map_payload` of a `final_response` WebSocket message
 * (LLD §5.2) into the marker/zone shapes MapPanel renders.
 *
 * Owner: P6 (Map/Trace). Implements the frontend half of FR-UI-2 / FR-GEO-3.
 *
 * `map_payload` crosses the wire as `{ markers: unknown[]; zones: unknown[] }`
 * (see wsClient.ts) and is populated backend-side by Synthesis (#68), so
 * entries are validated here rather than trusted: one malformed zone must not
 * take the whole Leaflet render down with it.
 */
import type { MarkerData, ZoneData } from "../components/Map/MapPanel";

/** Window event ChatPanel re-broadcasts each final_response's map_payload on. */
export const MAP_UPDATE_EVENT = "map_update";

export type MapState = {
  markers: MarkerData[];
  zones: ZoneData[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function toMarker(raw: unknown, index: number): MarkerData | null {
  if (!isRecord(raw)) return null;
  if (!isFiniteNumber(raw.lat) || !isFiniteNumber(raw.lng)) return null;

  return {
    id: typeof raw.id === "string" && raw.id ? raw.id : `marker-${index}`,
    lat: raw.lat,
    lng: raw.lng,
    label: typeof raw.label === "string" ? raw.label : "Queried location",
    isViolation: raw.isViolation === true,
    isProximity: raw.isProximity === true,
  };
}

/** A polygon needs at least three [lat, lng] points to be drawable. */
function toCoordinates(raw: unknown): [number, number][] | null {
  if (!Array.isArray(raw) || raw.length < 3) return null;

  const points: [number, number][] = [];
  for (const point of raw) {
    if (!Array.isArray(point) || point.length < 2) return null;
    const [lat, lng] = point;
    if (!isFiniteNumber(lat) || !isFiniteNumber(lng)) return null;
    points.push([lat, lng]);
  }
  return points;
}

function toZone(raw: unknown, index: number): ZoneData | null {
  if (!isRecord(raw)) return null;

  const coordinates = toCoordinates(raw.coordinates);
  if (!coordinates) return null;

  const zoneType = raw.zoneType;
  return {
    id: typeof raw.id === "string" && raw.id ? raw.id : `zone-${index}`,
    label: typeof raw.label === "string" ? raw.label : "Zone",
    coordinates,
    zoneType:
      zoneType === "PFZ" || zoneType === "IMBL" || zoneType === "MPA" ? zoneType : undefined,
    isViolation: raw.isViolation === true,
    isProximity: raw.isProximity === true,
  };
}

/**
 * Reads markers/zones out of a `map_payload`. Anything unrecognised is dropped
 * rather than rendered, and an absent/empty payload yields empty arrays — the
 * map shows nothing rather than stale or invented geometry (NFR-REL-1).
 */
export function normalizeMapPayload(payload: unknown): MapState {
  if (!isRecord(payload)) return { markers: [], zones: [] };

  const markers = Array.isArray(payload.markers)
    ? payload.markers
        .map(toMarker)
        .filter((marker): marker is MarkerData => marker !== null)
    : [];

  const zones = Array.isArray(payload.zones)
    ? payload.zones.map(toZone).filter((zone): zone is ZoneData => zone !== null)
    : [];

  return { markers, zones };
}
