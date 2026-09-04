
interface TraceStep {
  id: string;
  step: string;
  status: 'pending' | 'complete' | 'error';
}

const mockTraces: TraceStep[] = [
  { id: '1', step: 'Initializing agent orchestration', status: 'complete' },
  { id: '2', step: 'Querying route planner module', status: 'complete' },
  { id: '3', step: 'Awaiting telemetry stream response', status: 'pending' },
];

export default function TraceViewer() {
  return (
    <aside aria-label="Agent trace" style={{ padding: '1rem', background: '#1e1e1e', color: '#fff', borderRadius: '8px' }}>
      <h3 style={{ marginTop: 0 }}>Agent Trace</h3>
      <ul style={{ listStyle: 'none', padding: 0 }}>
        {mockTraces.map((trace) => (
          <li key={trace.id} style={{ marginBottom: '0.5rem', borderBottom: '1px solid #333', paddingBottom: '0.25rem' }}>
            <span style={{ marginRight: '0.5rem' }}>
              {trace.status === 'complete' ? '✅' : trace.status === 'pending' ? '⏳' : '❌'}
            </span>
            {trace.step}
          </li>
        ))}
      </ul>
    </aside>
  );
}