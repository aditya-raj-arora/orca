import { MapContainer, TileLayer, Marker, Popup, Polygon } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import markerIcon2x from "leaflet/dist/images/marker-icon-2x.png";
import markerIcon from "leaflet/dist/images/marker-icon.png";
import markerShadow from "leaflet/dist/images/marker-shadow.png";

delete (L.Icon.Default.prototype as unknown as { _getIconUrl?: () => string })._getIconUrl;
L.Icon.Default.mergeOptions({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
});

export interface MarkerData {
  id: string;
  lat: number;
  lng: number;
  label: string;
  isViolation?: boolean;
  isProximity?: boolean;
}

export interface ZoneData {
  id: string;
  coordinates: [number, number][];
  label: string;
  zoneType?: 'PFZ' | 'IMBL' | 'MPA';
  isViolation?: boolean;
  isProximity?: boolean;
}

interface MapPanelProps {
  markers?: MarkerData[];
  zones?: ZoneData[];
  weatherError?: boolean;
  oceanError?: boolean;
  geofenceError?: boolean;
}

const DEFAULT_CENTER: [number, number] = [10.0, 76.3];

export default function MapPanel({
  markers = [],
  zones = [],
  weatherError = false,
  oceanError = false,
  geofenceError = false
}: MapPanelProps) {
  const getMarkerIcon = (marker: MarkerData) => {
    if (!marker.isViolation && !marker.isProximity) return undefined; // default pin
    const color = marker.isViolation ? "#dc2626" : "#f59e0b";
    return L.divIcon({
      className: "",
      html: `<span style="display:block;width:16px;height:16px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 2px rgba(0,0,0,0.6);"></span>`,
      iconSize: [16, 16],
      iconAnchor: [8, 8],
    });
  };

  const getZoneStyle = (zone: ZoneData) => {
    if (zone.isViolation) {
      return {
        color: "#dc2626",
        fillColor: "#ef4444",
        fillOpacity: 0.5,
        weight: 3,
        dashArray: "6, 6"
      };
    }
    if (zone.isProximity) {
      return {
        color: "#f59e0b",
        fillColor: "#fbbf24",
        fillOpacity: 0.4,
        weight: 2
      };
    }
    if (zone.zoneType === 'PFZ') {
      return {
        color: "#10b981",
        fillColor: "#34d399",
        fillOpacity: 0.25,
        weight: 2
      };
    }
    return {
      color: "#3b82f6",
      fillColor: "#60a5fa",
      fillOpacity: 0.2,
      weight: 2
    };
  };

  // NFR-USE-3: a text/voice-equivalent of the map's content, for users who
  // can't (or don't) read the visual layer. Visually hidden but always
  // present in the DOM, so a screen reader can announce it; aria-live
  // re-announces it whenever markers/zones/errors change (e.g. a new query).
  const buildMapSummary = (): string => {
    const parts: string[] = [];

    if (markers.length === 0 && zones.length === 0) {
      parts.push("No location has been queried yet.");
    }

    markers.forEach((marker) => {
      let status = "";
      if (marker.isViolation) status = ", boundary violation detected";
      else if (marker.isProximity) status = ", near a restricted boundary";
      parts.push(
        `Marker: ${marker.label} at ${marker.lat.toFixed(3)}, ${marker.lng.toFixed(3)}${status}.`
      );
    });

    zones.forEach((zone) => {
      let status = "";
      if (zone.isViolation) status = ", violation detected";
      else if (zone.isProximity) status = ", proximity warning";
      parts.push(`Zone: ${zone.label}, type ${zone.zoneType || "Standard Zone"}${status}.`);
    });

    if (weatherError) parts.push("Weather data is unavailable for this query.");
    if (oceanError) parts.push("Ocean data is unavailable for this query.");
    if (geofenceError) parts.push("Geofence data is unavailable for this query.");

    return parts.join(" ");
  };

  return (
    <div
      role="region"
      aria-label="Map showing queried location, fishing zones, and maritime boundaries"
      style={{ position: "relative", height: "100%", width: "100%" }}
    >
      <p
        className="sr-only"
        aria-live="polite"
      >
        {buildMapSummary()}
      </p>
      <div style={{
        position: "absolute",
        top: "10px",
        right: "10px",
        zIndex: 1000,
        display: "flex",
        flexDirection: "column",
        gap: "6px"
      }}>
        {weatherError && (
          <div style={{ background: "#fee2e2", color: "#991b1b", padding: "6px 10px", borderRadius: "4px", fontSize: "12px", border: "1px solid #f87171" }}>
            Weather Data Unavailable
          </div>
        )}
        {oceanError && (
          <div style={{ background: "#fee2e2", color: "#991b1b", padding: "6px 10px", borderRadius: "4px", fontSize: "12px", border: "1px solid #f87171" }}>
            Ocean Data Unavailable
          </div>
        )}
        {geofenceError && (
          <div style={{ background: "#fee2e2", color: "#991b1b", padding: "6px 10px", borderRadius: "4px", fontSize: "12px", border: "1px solid #f87171" }}>
            Geofence Data Unavailable
          </div>
        )}
      </div>

      <MapContainer center={DEFAULT_CENTER} zoom={8} style={{ height: "100%", width: "100%" }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {markers.map((marker) => (
          <Marker
            key={marker.id}
            position={[marker.lat, marker.lng]}
            icon={getMarkerIcon(marker)}
          >
            <Popup>
              <div>
                {marker.label}
                {marker.isViolation && (
                  <span style={{ color: "red", display: "block" }}>Status: Violation Detected</span>
                )}
                {marker.isProximity && (
                  <span style={{ color: "orange", display: "block" }}>Status: Proximity Warning</span>
                )}
              </div>
            </Popup>
          </Marker>
        ))}

        {zones.map((zone) => (
          <Polygon key={zone.id} positions={zone.coordinates} pathOptions={getZoneStyle(zone)}>
            <Popup>
              <div>
                <strong>{zone.label}</strong>
                <br />
                Type: {zone.zoneType || 'Standard Zone'}
                {zone.isViolation && <span style={{ color: 'red', display: 'block' }}>Status: Violation Detected</span>}
                {zone.isProximity && <span style={{ color: 'orange', display: 'block' }}>Status: Proximity Warning</span>}
              </div>
            </Popup>
          </Polygon>
        ))}
      </MapContainer>
    </div>
  );
}