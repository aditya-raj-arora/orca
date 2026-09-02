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

interface MarkerData {
  id: string;
  lat: number;
  lng: number;
  label: string;
}

interface ZoneData {
  id: string;
  coordinates: [number, number][];
  label: string;
}

interface MapPanelProps {
  markers?: MarkerData[];
  zones?: ZoneData[];
}

const DEFAULT_CENTER: [number, number] = [10.0, 76.3];

const MOCK_MARKERS: MarkerData[] = [
  { id: "1", lat: 10.0, lng: 76.3, label: "Queried Location (Kochi Centroid)" }
];

const MOCK_ZONES: ZoneData[] = [
  {
    id: "mpa-1",
    label: "Mock MPA Zone",
    coordinates: [
      [9.9, 76.1],
      [10.1, 76.1],
      [10.1, 76.4],
      [9.9, 76.4]
    ]
  }
];

export default function MapPanel({
  markers = MOCK_MARKERS,
  zones = MOCK_ZONES
}: MapPanelProps) {
  return (
    <MapContainer center={DEFAULT_CENTER} zoom={8} style={{ height: "100%", width: "100%" }}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />

      {markers.map((marker) => (
        <Marker key={marker.id} position={[marker.lat, marker.lng]}>
          <Popup>{marker.label}</Popup>
        </Marker>
      ))}

      {zones.map((zone) => (
        <Polygon key={zone.id} positions={zone.coordinates} pathOptions={{ color: "red" }}>
          <Popup>{zone.label}</Popup>
        </Polygon>
      ))}
    </MapContainer>
  );
}