/**
 * Conversational chat interface — voice + text input/output, multi-turn
 * history display.
 *
 * Owner: P5 (Frontend Engineer, Core UI).
 * Implements: FR-UI-1, FR-UI-4.
 * Reference: HLD v1.0 §3 "Web Client", LLD v1.0 §5.2 (WebSocket message shapes).
 */
export default function ChatPanel() {
  // TODO(P5):
  //   1. Connect to /ws/v1/query/{session_id} (see src/api/ — build a small
  //      typed client wrapping the LLD §5.2 message schema, don't inline raw
  //      WebSocket calls in this component).
  //   2. Mic capture -> base64 audio -> send {type:"query", mode:"voice", ...}
  //      (Web Audio API, per HLD §6 tech stack).
  //   3. Render streamed trace_update messages by handing them off to
  //      TraceViewer (owned by P6) — don't duplicate trace-rendering logic
  //      here.
  //   4. Render final_response: text, and play audio_base64 via TTS playback
  //      for voice-mode interactions (FR-LANG-5).
  //   5. Multi-turn history (FR-UI-4): fetch GET /api/v1/session/{id}/history
  //      on mount, render prior turns above the live conversation.
  //   6. Accessibility (NFR-USE-3): every visual element needs a text/voice
  //      equivalent — do not rely on color alone for the verdict banner.
  return (
    <section aria-label="Conversation">
      <p>TODO(P5): chat UI — see component doc comment for the task breakdown.</p>
    </section>
  );
}
