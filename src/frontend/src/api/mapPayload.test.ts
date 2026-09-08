/**
 * Covers the map_payload -> MapPanel props contract (LLD §5.2, FR-UI-2,
 * FR-GEO-3, #35). Owner: P6.
 */
import { describe, expect, it } from "vitest";
import { normalizeMapPayload } from "./mapPayload";

// Shape as it actually arrives on the wire: nested under final_response's
// map_payload, not at the top level of the message.
const MAP_PAYLOAD = {
  markers: [{ id: "m1", lat: 10.0, lng: 76.3, label: "Queried Location" }],
  zones: [
    {
      id: "pfz-1",
      label: "Nearest PFZ",
      zoneType: "PFZ",
      coordinates: [
        [9.9, 76.1],
        [10.1, 76.1],
        [10.1, 76.4],
      ],
    },
  ],
};

describe("normalizeMapPayload", () => {
  it("reads markers and zones out of a final_response map_payload", () => {
    const { markers, zones } = normalizeMapPayload(MAP_PAYLOAD);

    expect(markers).toEqual([
      { id: "m1", lat: 10.0, lng: 76.3, label: "Queried Location", isViolation: false, isProximity: false },
    ]);
    expect(zones).toHaveLength(1);
    expect(zones[0]).toMatchObject({ id: "pfz-1", label: "Nearest PFZ", zoneType: "PFZ" });
    expect(zones[0].coordinates).toHaveLength(3);
  });

  it("keeps the violation / proximity flags that drive FR-GEO-3 styling", () => {
    const { zones } = normalizeMapPayload({
      zones: [
        { id: "mpa-1", label: "MPA", zoneType: "MPA", isViolation: true, coordinates: MAP_PAYLOAD.zones[0].coordinates },
        { id: "imbl-1", label: "IMBL", zoneType: "IMBL", isProximity: true, coordinates: MAP_PAYLOAD.zones[0].coordinates },
      ],
    });

    expect(zones[0]).toMatchObject({ zoneType: "MPA", isViolation: true, isProximity: false });
    expect(zones[1]).toMatchObject({ zoneType: "IMBL", isViolation: false, isProximity: true });
  });

  it("keeps a marker's violation / proximity flags too (#163 map markers)", () => {
    const { markers } = normalizeMapPayload({
      markers: [
        { id: "geofence-violation", lat: 9.0, lng: 79.1, label: "Violation", isViolation: true },
        { id: "geofence-proximity", lat: 9.1, lng: 79.2, label: "Proximity", isProximity: true },
      ],
    });

    expect(markers[0]).toMatchObject({ isViolation: true, isProximity: false });
    expect(markers[1]).toMatchObject({ isViolation: false, isProximity: true });
  });

  it("returns empty arrays for an absent or empty payload rather than inventing geometry", () => {
    expect(normalizeMapPayload(undefined)).toEqual({ markers: [], zones: [] });
    expect(normalizeMapPayload({})).toEqual({ markers: [], zones: [] });
    expect(normalizeMapPayload({ markers: [], zones: [] })).toEqual({ markers: [], zones: [] });
  });

  it("ignores markers/zones at the top level of the message", () => {
    // The old bug: reading msg.markers instead of msg.map_payload.markers.
    const finalResponse = { type: "final_response", text: "…", map_payload: MAP_PAYLOAD };

    expect(normalizeMapPayload(finalResponse)).toEqual({ markers: [], zones: [] });
    expect(normalizeMapPayload(finalResponse.map_payload).markers).toHaveLength(1);
  });

  it("drops malformed entries instead of handing Leaflet something it will throw on", () => {
    const { markers, zones } = normalizeMapPayload({
      markers: [{ id: "ok", lat: 1, lng: 2, label: "fine" }, { id: "no-coords" }, null],
      zones: [
        { id: "no-coords", label: "missing coordinates" },
        { id: "too-few", label: "not a polygon", coordinates: [[1, 2], [3, 4]] },
        { id: "nan", label: "bad point", coordinates: [[1, 2], [3, "x"], [5, 6]] },
      ],
    });

    expect(markers).toEqual([
      { id: "ok", lat: 1, lng: 2, label: "fine", isViolation: false, isProximity: false },
    ]);
    expect(zones).toEqual([]);
  });
});
