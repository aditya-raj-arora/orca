/**
 * Interactive map: queried location, PFZ zone(s), geofencing boundaries.
 *
 * Owner: P6 (Frontend Engineer, Map/Trace + QA/Integration Lead).
 * Implements: FR-UI-2.
 * Reference: HLD v1.0 §6 (Leaflet), LLD v1.0 §5.2 (map_payload shape in the
 * final_response message — keep this in sync with
 * src/backend/app/schemas/synthesis.py MapPayload).
 */
import { MapContainer, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";

const DEFAULT_CENTER: [number, number] = [10.0, 76.3]; // Kochi-ish, placeholder

export default function MapPanel() {
  // TODO(P6):
  //   1. Accept `markers` and `zones` (from ComposedResponse.map_payload,
  //      LLD §5.2) as props once wired up from App.tsx's session state.
  //   2. Render markers for queried location + nearest PFZ centroid
  //      (FR-OCEAN-3).
  //   3. Render geofence zone overlays (MPA polygons, IMBL buffer) with a
  //      visually distinct, unmissable style for violations (FR-GEO-3 —
  //      this must be as unambiguous on the map as it is in the text).
  //   4. Add a text-equivalent legend/description for each layer (NFR-USE-3
  //      accessibility requirement — map content isn't voice/text-readable by
  //      default).
  return (
    <MapContainer center={DEFAULT_CENTER} zoom={7} style={{ height: "100%", width: "100%" }}>
      <TileLayer
        attribution='&copy; OpenStreetMap contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {/* TODO(P6): marker/zone layers go here */}
    </MapContainer>
  );
}
