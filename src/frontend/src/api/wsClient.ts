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
  // null = no verdict was requested (#126: an informational-only query, e.g.
  // plain weather, never invokes Risk/Safety) — distinct from
  // "INSUFFICIENT_DATA", which means Risk/Safety WAS asked and couldn't
  // reach one. ChatPanel's `{m.verdict && (...)}` guard already treats null
  // the same as "no badge", so this needed no rendering-logic change.
  verdict: "SAFE" | "CAUTION" | "UNSAFE" | "INSUFFICIENT_DATA" | null;
  citations: { source: string; timestamp: string }[];
  map_payload: { markers: unknown[]; zones: unknown[] };
};

export type ServerErrorMessage = { type: "error"; message: string };

export type ServerMessage = ServerTraceUpdate | ServerFinalResponse | ServerErrorMessage;

type MessageCallback = (msg: ServerMessage) => void;

// #170: surfaced to the UI so it can show a "reconnecting" state instead of
// looking like it silently hung. "connecting" covers both the first connect
// and every reconnect attempt; "closed" is the gap between them.
export type ConnectionStatus = "connecting" | "open" | "closed";
type StatusCallback = (status: ConnectionStatus) => void;

class WSClient {
  private ws: WebSocket | null = null;
  private sessionId: string | null = null;
  private onMessageCb: MessageCallback | null = null;
  private onStatusCb: StatusCallback | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  // #170: the backend closes the socket after every query (one query per
  // connection, main.py's query_socket) and the client reconnects with a
  // backoff. A query sent inside that reconnect window used to be silently
  // dropped ("WebSocket is not open. Cannot send message.") with nothing in
  // the UI reflecting it, so the trace panel sat on "Awaiting..." forever.
  // Queue it instead and flush once the new connection is open.
  private pendingQueue: ClientQueryMessage[] = [];

  connect(sessionId: string, onMessage: MessageCallback, onStatus?: StatusCallback) {
    this.sessionId = sessionId;
    this.onMessageCb = onMessage;
    this.onStatusCb = onStatus ?? null;
    this._connect();
  }

  private _connect() {
    if (!this.sessionId) return;
    this.onStatusCb?.("connecting");

    const baseUrl = import.meta.env.VITE_WS_BASE_URL ?? "";
    let urlStr = `${baseUrl}/ws/v1/query/${this.sessionId}`;

    if (!urlStr.startsWith("ws")) {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      urlStr = `${protocol}//${window.location.host}${urlStr}`;
    }

    this.ws = new WebSocket(urlStr);

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as ServerMessage;
        if (this.onMessageCb) this.onMessageCb(msg);
      } catch (e) {
        console.error("Failed to parse WS message", e);
      }
    };

    this.ws.onopen = () => {
      console.log("WebSocket connected.");
      this.reconnectAttempts = 0;
      this.onStatusCb?.("open");
      this._flushQueue();
    };

    this.ws.onclose = () => {
      console.log("WebSocket closed.");
      this.onStatusCb?.("closed");
      this.scheduleReconnect();
    };

    this.ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };
  }

  private scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error("Max WebSocket reconnect attempts reached.");
      return;
    }
    const delay = Math.pow(2, this.reconnectAttempts) * 1000;
    this.reconnectAttempts++;
    console.log(`Reconnecting WebSocket in ${delay}ms...`);
    setTimeout(() => this._connect(), delay);
  }

  private _flushQueue() {
    if (this.pendingQueue.length === 0) return;
    const queued = this.pendingQueue;
    this.pendingQueue = [];
    for (const msg of queued) {
      this.ws?.send(JSON.stringify(msg));
    }
  }

  send(msg: ClientQueryMessage) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    } else {
      console.warn("WebSocket not open yet — queuing message until reconnected.");
      this.pendingQueue.push(msg);
    }
  }

  close() {
    this.pendingQueue = [];
    if (this.ws) {
      this.ws.onclose = null; // Prevent reconnect loop
      this.ws.close();
      this.ws = null;
    }
  }
}

export const wsClient = new WSClient();
