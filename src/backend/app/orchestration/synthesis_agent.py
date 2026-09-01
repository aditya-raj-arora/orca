"""
Synthesis Agent — composes all invoked agents' outputs into one coherent,
cited, natural-language response.

Owner: P2 (LLM/Synthesis Engineer).
Implements: FR-SYN-1 to FR-SYN-3.
Reference: LLD v1.0 §2.7.

SAFETY-CRITICAL RULE (FR-SYN-2): every sentence in the output must map to a
citation. Do not ship this without the citation-coverage check described below
— an uncited claim in a safety-relevant response is the single worst failure
mode this system can have.
"""
from __future__ import annotations

from app.schemas.risk import RiskVerdict
from app.schemas.synthesis import ComposedResponse, ExecutionPlan


class SynthesisAgent:
    def compose(
        self,
        plan: ExecutionPlan,
        results: dict[str, object],  # agent_name -> WeatherResult|PFZResult|GeofenceResult|RiskVerdict
        language: str,
    ) -> ComposedResponse:
        """
        TODO(P2):
          1. Build an LLM prompt constrained to ONLY the facts present in
             `results` — do not let the model add outside knowledge (FR-SYN-1).
          2. Generate `text` in `language` (already resolved by Bhashini /
             app/language/bhashini_client.py upstream — this method receives a
             plain language code, it does not call Bhashini itself).
          3. Run the post-generation citation-coverage check: parse `text`
             sentence-by-sentence, confirm each maps to an entry in `citations`;
             if any sentence has no backing citation, regenerate (LLD §2.7) —
             do not silently strip the sentence, and do not ship it uncited.
          4. Build map_payload (markers/zones) from the geofence/ocean results —
             coordinate the exact shape with P6 (owns the Leaflet rendering
             consuming this, see app/schemas/synthesis.py MapPayload TODO).
          5. Copy `plan.trace` forward into the response so the frontend trace
             viewer (FR-UI-3, owned by P6) has the full decision history.

        Reminder (NFR-REL-2 / FR-RISK-3): if results contains a RiskVerdict with
        verdict == 'INSUFFICIENT_DATA', the composed text must say so plainly —
        never phrase an insufficient-data verdict in a way that reads as "safe".
        """
        raise NotImplementedError

    def _citation_coverage_ok(self, text: str, results: dict[str, object]) -> bool:
        """TODO(P2): implement the coverage check described in compose() step 3."""
        raise NotImplementedError
