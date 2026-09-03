import { useState, useEffect } from "react";
import ChatPanel from "./components/Chat/ChatPanel";
import MapPanel, { MarkerData, ZoneData } from "./components/Map/MapPanel";
import TraceViewer from "./components/TraceViewer/TraceViewer";

export default function App() {
  const [markers, setMarkers] = useState<MarkerData[]>([]);
  const [zones, setZones] = useState<ZoneData[]>([]);

  useEffect(() => {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsHost = window.location.hostname || "localhost";
    const wsPort = "8000";
    const wsUrl = `${wsProtocol}//${wsHost}:${wsPort}/ws`;

    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.markers) {
          setMarkers(data.markers);
        }
        if (data.zones) {
          setZones(data.zones);
        }
      } catch (err) {
        console.error("Failed to parse WebSocket message", err);
      }
    };

    return () => {
      ws.close();
    };
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