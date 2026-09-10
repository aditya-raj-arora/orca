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
  // #170: a query sent while the socket isn't open used to be silently
  // dropped ("WebSocket is not open. Cannot send message.") with nothing in
  // the UI reflecting it, so the trace panel sat on "Awaiting..." forever.
  // Queue it instead and flush once the connection is open.
  //
  // The backend now holds ONE connection open across many turns (main.py's
  // query_socket receive loop), so this queue is for genuine drops —
  // reconnects, network blips, a sleeping laptop — not for every single
  // query as it was when the server closed after each one.
  private pendingQueue: ClientQueryMessage[] = [];
  // The query we've sent but haven't seen a terminal message for yet.
  //
  // readyState === OPEN is NOT proof the peer is still listening: a socket
  // whose server side is gone (or whose close frame never made it back
  // through a proxy) still reports OPEN, and send() on it succeeds silently
  // into nowhere. That is exactly how the "second prompt spins on Thinking
  // forever" bug worked — the query vanished and nothing ever resent it.
  // Holding onto the in-flight query means a close can put it BACK on the
  // queue to be replayed, instead of losing it with the connection.
  // Replaying is safe here: queries are read-only lookups, so a duplicate
  // costs a little work, whereas a lost one costs the user their whole turn.
  private inFlight: ClientQueryMessage | null = null;

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
        // This turn is finished — nothing left to replay if we drop now.
        if (msg.type === "final_response" || msg.type === "error") {
          this.inFlight = null;
        }
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
      // A query that was still awaiting its answer went down with the
      // socket. Put it at the front of the queue so the reconnect replays
      // it, rather than leaving the UI waiting for a reply that can never
      // arrive.
      if (this.inFlight) {
        this.pendingQueue.unshift(this.inFlight);
        this.inFlight = null;
      }
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
      this.inFlight = msg;
    }
  }

  send(msg: ClientQueryMessage) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      this.inFlight = msg;
    } else {
      console.warn("WebSocket not open yet — queuing message until reconnected.");
      this.pendingQueue.push(msg);
    }
  }

  close() {
    this.pendingQueue = [];
    this.inFlight = null;
    if (this.ws) {
      this.ws.onclose = null; // Prevent reconnect loop
      this.ws.close();
      this.ws = null;
    }
  }
}

export const wsClient = new WSClient();
