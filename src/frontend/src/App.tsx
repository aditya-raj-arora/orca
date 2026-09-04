/**
 * Top-level layout: chat panel + map panel + agent-trace panel.
 *
 * Owners: P5 (Chat, split left) + P6 (Map/Trace, split right) — split layout
 * ownership matches CODEOWNERS. Coordinate on the shared shell below rather
 * than each rewriting App.tsx independently.
 *
 * Reference: HLD v1.0 §3 "Web Client", FR-UI-1 to FR-UI-4.
 */
import { useState, useEffect } from "react";
import ChatPanel from "./components/Chat/ChatPanel";
import MapPanel from "./components/Map/MapPanel";
import type { MarkerData, ZoneData } from "./components/Map/MapPanel";
import TraceViewer from "./components/TraceViewer/TraceViewer";
import { MAP_UPDATE_EVENT, normalizeMapPayload } from "./api/mapPayload";

export default function App() {
  const [markers, setMarkers] = useState<MarkerData[]>([]);
  const [zones, setZones] = useState<ZoneData[]>([]);

  // ChatPanel owns the single wsClient connection (LLD §5.2 — one query per
  // connection, session-scoped) and re-broadcasts each final_response's
  // map_payload as a window event, mirroring its existing trace_update
  // dispatch. Listening here keeps the map on live results without opening a
  // second socket against the same backend.
  useEffect(() => {
    const handleMapUpdate = (event: Event) => {
      const { markers: nextMarkers, zones: nextZones } = normalizeMapPayload(
        (event as CustomEvent<unknown>).detail
      );
      setMarkers(nextMarkers);
      setZones(nextZones);
    };

    window.addEventListener(MAP_UPDATE_EVENT, handleMapUpdate);
    return () => window.removeEventListener(MAP_UPDATE_EVENT, handleMapUpdate);
  }, []);

  return (
    <div className="app-layout">
      <header className="app-header">
        <h1>ORCA</h1>
        <span className="subtitle">Marine Intelligence</span>
      </header>
      <div className="main-content">
        <div className="glass-panel chat-panel-container">
          <ChatPanel />
        </div>
        <div className="glass-panel map-trace-container">
          <MapPanel markers={markers} zones={zones} />
          <TraceViewer />
        </div>
      </div>
    </div>
  );
}
