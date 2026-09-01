import express from "express";
import { WebSocketServer } from "ws";
import http from "http";

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server });

// Mock history endpoint
app.get("/api/v1/session/:id/history", (_req, res) => {
  res.json([]);
});

// Mock WebSocket — returns a ServerFinalResponse matching LLD §5.2
wss.on("connection", (ws, req) => {
  console.log("Mock WS connection:", req.url);

  ws.on("message", (message) => {
    try {
      const data = JSON.parse(message);
      console.log("Received:", data);

      if (data.type === "query") {
        // Simulate a final_response after a short delay
        setTimeout(() => {
          ws.send(JSON.stringify({
            type: "final_response",
            text: "Conditions near Kochi are currently clear. Sea state is calm with wave heights below 1.5 m. PFZ advisory suggests good fishing potential 12 nm southwest.",
            language: "en",
            verdict: "SAFE",
            citations: [{ source: "INCOIS PFZ Advisory", timestamp: "2026-09-01T12:00:00Z" }],
            map_payload: { markers: [], zones: [] }
          }));
        }, 500);
      }
    } catch (e) {
      console.error("Parse error:", e);
    }
  });
});

const PORT = 8000;
server.listen(PORT, () => {
  console.log(`Mock backend listening on http://localhost:${PORT}`);
});
