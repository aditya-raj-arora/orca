/**
 * Typed wrapper around the /ws/v1/query/{session_id} WebSocket contract.
 *
 * Owner: P5 (primary consumer — Chat), shared with P6 (Trace/Map consume the
 * same stream). Keep message shapes here in lockstep with
 * src/backend/app/main.py and LLD v1.0 §5.2 — this is a shared contract per
 * CONTRIBUTING.md §6, don't let frontend and backend drift independently.
 */

export type ClientQueryMessage = {
  type: "query";
  mode: "voice" | "text";
  audio_base64?: string;
  text?: string;
  location_hint?: { lat: number; lon: number };
};

export type ServerTraceUpdate = { type: "trace_update"; step: string };

export type ServerFinalResponse = {
  type: "final_response";
  text: string;
  language: string;
  audio_base64?: string;
  verdict: "SAFE" | "CAUTION" | "UNSAFE" | "INSUFFICIENT_DATA";
  citations: { source: string; timestamp: string }[];
  map_payload: { markers: unknown[]; zones: unknown[] };
};

export type ServerErrorMessage = { type: "error"; message: string };

export type ServerMessage = ServerTraceUpdate | ServerFinalResponse | ServerErrorMessage;

// TODO(P5): implement connect()/send()/onMessage() around a real WebSocket,
// using WS_BASE_URL (see ./config.ts) as the origin in production, with
// reconnect handling appropriate for the low-bandwidth, potentially
// high-latency coastal network conditions called out in SRS §2.4.
export function connect(_sessionId: string): void {
  throw new Error("TODO(P5): implement WebSocket client — see wsClient.ts doc comment");
}
