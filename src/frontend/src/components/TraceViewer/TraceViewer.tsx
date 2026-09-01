/**
 * Live agent-orchestration trace panel — which agents were invoked, in what
 * order, and why.
 *
 * Owner: P6 (Frontend Engineer, Map/Trace + QA/Integration Lead).
 * Implements: FR-UI-3, surfaces FR-PLAN-4's human-readable trace.
 * Reference: LLD v1.0 §5.2 (`trace_update` streamed WebSocket messages).
 */
export default function TraceViewer() {
  // TODO(P6):
  //   1. Accept a stream/array of trace_update messages
  //      ({type:"trace_update", step: string}, LLD §5.2) as props.
  //   2. Render as an ordered, appending list (static placeholder first per
  //      the Chat Summary Day 3 plan: "Agent-trace panel (static -> live
  //      later)" — wire to the real WebSocket stream once P1's backend
  //      streaming is up, Day 4).
  //   3. Visually distinguish steps that are still pending vs. complete vs.
  //      errored/unavailable (ties to NFR-REL-1 — the UI must show, not hide,
  //      a data-source failure).
  return (
    <aside aria-label="Agent trace">
      <p>TODO(P6): live agent-trace viewer — see component doc comment.</p>
    </aside>
  );
}
