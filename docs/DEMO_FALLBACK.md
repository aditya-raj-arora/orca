# ORCA: Demo Contingency & Fallback Plan

## 1. Pre-Demo Warm-Up (Cold-Start Mitigation)
**Owner:** P6 (QA/Integration Lead)
To satisfy NFR-PERF response budgets (8s/15s) and prevent Render/Supabase free-tier sleep cycles:
* **T-Minus 15 mins:** Send a manual GET request to the `orca-backend` `/health` endpoint to wake the Render instance.
* **T-Minus 15 mins:** Execute a basic query against Supabase (e.g., checking `geofence_boundary`) to wake the database from idle.

## 2. External Dependency Failures

### Scenario A: Open-Meteo API Rate Limited (Shared IP Egress)
* **Visual:** Weather module displays `INSUFFICIENT_DATA` for marine/wave data.
* **Action:** Do not manually refresh or retry (IP-level rate limit means retries add load without refilling buckets).
* **Talking Point:** "Because our deployment shares a free-tier egress IP on Render, we can hit Open-Meteo rate limits. Notice how the application does not crash; instead, it falls back to WeatherAPI for available legs and gracefully degrades to show 'Insufficient Data' for wave heights without breaking the core telemetry."
* **Asset:** Switch to a pre-cached screenshot of the fully populated ORCA weather dashboard.

### Scenario B: Supabase Database or WebSocket Disconnect
* **Visual:** The UI transitions to the disconnected error state.
* **Action:** Highlight the custom error-state UI.
* **Talking Point:** "If our Supabase connection drops, our custom WebSocket disconnect handler immediately catches it and alerts the operator with a clean error-state UI rather than freezing the dashboard."
* **Asset:** Have `docker compose up` running locally as a hot-standby mirror to switch screens if cloud connectivity completely fails.

### Scenario C: LLM or Bhashini Timeout
* **Visual:** Query times out or logs a degraded agent.
* **Action:** Point out performance budget compliance.
* **Talking Point:** "We enforce strict latency budgets. If an external AI or translation call exceeds our threshold, the ingestion layer interrupts it and falls back gracefully so core mapping features remain responsive."