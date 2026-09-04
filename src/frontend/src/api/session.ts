/**
 * Session lifecycle against the Gateway (LLD §5.1 `POST /api/v1/session`,
 * §5.2 `GET /api/v1/session/{id}/history`).
 *
 * Owner: P5. Added for #104.
 *
 * WHY THIS EXISTS: ChatPanel used to mint its own id
 * (`sess_${Math.random().toString(36).substring(2, 9)}`) and never register
 * it, so the backend only learned the id when the WebSocket connected and
 * `GET .../history` 404'd on every first load for every user. Two ~7-char
 * base36 ids from a non-crypto RNG can also collide, and colliding clients
 * share one server-side ConversationContext — which holds last-known-location
 * (FR-PLAN-5). The backend already mints a uuid4; this module just asks it to.
 *
 * Owner: P5.
 */
import { API_BASE_URL } from "./config";

export type SessionHistoryStatus = "ok" | "unknown-session" | "unreachable";

/** LLD §5.1 response shape. */
type SessionResponse = {
  session_id: string;
  created_at: string;
};

/**
 * Registers a new session with the backend and returns its id.
 *
 * Falls back to a locally generated uuid if the Gateway can't be reached, so
 * a backend outage degrades to "the chat UI still loads" rather than a blank
 * screen — consistent with the degrade-don't-block posture the backend takes
 * everywhere (LLD §6). `crypto.randomUUID()` rather than `Math.random()`:
 * same collision properties as the server's uuid4, and it is available in
 * every browser we target since the app is served over HTTPS.
 */
export async function createSession(): Promise<string> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/session`, { method: "POST" });
    if (!res.ok) throw new Error(`session create failed: ${res.status}`);
    const body = (await res.json()) as SessionResponse;
    if (!body.session_id) throw new Error("session create returned no session_id");
    return body.session_id;
  } catch (err) {
    console.warn("Could not create a backend session, using a local id.", err);
    return crypto.randomUUID();
  }
}

/**
 * Asks the backend whether it still knows this session.
 *
 * Three outcomes rather than a boolean, because they need different handling:
 * `unknown-session` (404) means the id is stale — the backend restarted, or
 * it is a pre-#104 client-minted id — and the caller should start fresh.
 * `unreachable` means we learned nothing, so the caller should NOT throw away
 * local state on the strength of it.
 *
 * Note the backend's history is in-process (`main.py`'s `_SESSIONS`;
 * `app/db/session_repo.py` is still NotImplementedError), so `unknown-session`
 * is the normal outcome after any backend restart — on Render's free plan,
 * after any spin-down.
 */
export async function checkSession(sessionId: string): Promise<SessionHistoryStatus> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/session/${sessionId}/history`);
    if (res.status === 404) return "unknown-session";
    if (!res.ok) return "unreachable";
    return "ok";
  } catch (err) {
    console.warn("Could not reach the backend to verify the session.", err);
    return "unreachable";
  }
}
