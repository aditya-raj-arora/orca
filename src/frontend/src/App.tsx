/**
 * Top-level layout: chat panel + map panel + agent-trace panel.
 *
 * Owners: P5 (Chat, split left) + P6 (Map/Trace, split right) — split layout
 * ownership matches CODEOWNERS. Coordinate on the shared shell below rather
 * than each rewriting App.tsx independently.
 *
 * Reference: HLD v1.0 §3 "Web Client" row, FR-UI-1 to FR-UI-4.
 *
 * (Trivial edit — verifying the CI -> deploy-frontend gate end-to-end.)
 */
import ChatPanel from "./components/Chat/ChatPanel";
import MapPanel from "./components/Map/MapPanel";
import TraceViewer from "./components/TraceViewer/TraceViewer";

export default function App() {
  // TODO(P5/P6): lift shared session/query state up here (or into a context /
  // small store in src/state/) once ChatPanel needs to trigger MapPanel and
  // TraceViewer updates from the same WebSocket stream (LLD §5.2).
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", height: "100vh" }}>
      <ChatPanel />
      <div style={{ display: "grid", gridTemplateRows: "2fr 1fr" }}>
        <MapPanel />
        <TraceViewer />
      </div>
    </div>
  );
}
