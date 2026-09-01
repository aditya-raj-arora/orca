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

type MessageCallback = (msg: ServerMessage) => void;

class WSClient {
  private ws: WebSocket | null = null;
  private sessionId: string | null = null;
  private onMessageCb: MessageCallback | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;

  connect(sessionId: string, onMessage: MessageCallback) {
    this.sessionId = sessionId;
    this.onMessageCb = onMessage;
    this._connect();
  }

  private _connect() {
    if (!this.sessionId) return;
    
    // In dev, WS_BASE_URL might be empty string relying on relative path proxy.
    // WebSocket constructor requires an absolute URL.
    let urlStr = "";
    // If we have an import for WS_BASE_URL, we'd use it here. 
    // Since we didn't import it in this block, let's just assume we can import it.
    // Let's actually import WS_BASE_URL at the top of the file in another chunk, 
    // or just rely on relative protocol resolution.
    // Assuming WS_BASE_URL is imported or we can just read import.meta.env
    const baseUrl = import.meta.env.VITE_WS_BASE_URL ?? "";
    urlStr = `${baseUrl}/ws/v1/query/${this.sessionId}`;
    
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
    };

    this.ws.onclose = () => {
      console.log("WebSocket closed.");
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

  send(msg: ClientQueryMessage) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    } else {
      console.error("WebSocket is not open. Cannot send message.");
    }
  }
}

export const wsClient = new WSClient();
