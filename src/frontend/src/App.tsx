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
  const [showMobileMap, setShowMobileMap] = useState(false);
  const [isDark, setIsDark] = useState(() => {
    return localStorage.getItem("orca_theme") === "dark";
  });
  const [markers, setMarkers] = useState<MarkerData[]>([]);
  const [zones, setZones] = useState<ZoneData[]>([]);

  useEffect(() => {
    if (isDark) {
      document.body.classList.add("dark");
      localStorage.setItem("orca_theme", "dark");
    } else {
      document.body.classList.remove("dark");
      localStorage.setItem("orca_theme", "light");
    }
  }, [isDark]);

  // Fix for Leaflet map glitching when toggled from display: none
  useEffect(() => {
    const timer = setTimeout(() => {
      window.dispatchEvent(new Event('resize'));
    }, 100);
    return () => clearTimeout(timer);
  }, [showMobileMap]);

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
        <div className="header-left">
          <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: "var(--color-primary)" }}>
            <path d="M2 6c.6 0 1.2-.2 1.7-.6l2.3-2A4 4 0 0 1 8.6 3c.8 0 1.5.3 2.1.8l2.6 2.3c.5.5 1.2.7 1.9.7.7 0 1.4-.2 2-.7l2.5-2.2A4 4 0 0 1 22 3"></path>
            <path d="M2 12c.6 0 1.2-.2 1.7-.6l2.3-2A4 4 0 0 1 8.6 9c.8 0 1.5.3 2.1.8l2.6 2.3c.5.5 1.2.7 1.9.7.7 0 1.4-.2 2-.7l2.5-2.2A4 4 0 0 1 22 9"></path>
            <path d="M2 18c.6 0 1.2-.2 1.7-.6l2.3-2A4 4 0 0 1 8.6 15c.8 0 1.5.3 2.1.8l2.6 2.3c.5.5 1.2.7 1.9.7.7 0 1.4-.2 2-.7l2.5-2.2A4 4 0 0 1 22 15"></path>
          </svg>
          <h1>ORCA</h1>
          <span className="subtitle">Marine Assistant</span>
        </div>

        <div className="header-right">
          <button
            className="mobile-view-toggle"
            onClick={() => setShowMobileMap(!showMobileMap)}
            aria-label="Toggle Map View"
          >
            {showMobileMap ? (
              <>
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                </svg>
                <span>Chat</span>
              </>
            ) : (
              <>
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"></polygon>
                  <line x1="9" y1="3" x2="9" y2="21"></line>
                  <line x1="15" y1="3" x2="15" y2="21"></line>
                </svg>
                <span>Map</span>
              </>
            )}
          </button>

          <button className="theme-toggle" onClick={() => setIsDark(!isDark)} aria-label="Toggle theme">
            {isDark ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5"></circle>
                <line x1="12" y1="1" x2="12" y2="3"></line>
                <line x1="12" y1="21" x2="12" y2="23"></line>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                <line x1="1" y1="12" x2="3" y2="12"></line>
                <line x1="21" y1="12" x2="23" y2="12"></line>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
              </svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
              </svg>
            )}
          </button>
          <div className="status-dot" style={{ marginLeft: '0.5rem' }}></div>
          <span>System Online</span>
        </div>
      </header>

      <div className={`main-content ${showMobileMap ? 'show-map' : 'show-chat'}`}>
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
