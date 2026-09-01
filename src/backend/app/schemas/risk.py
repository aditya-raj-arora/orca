"""
RiskVerdict contract — owned by P4 (Geospatial & Risk Engineer).

Reference: LLD v1.0 §2.6, §4.2 (Figure 2), implements FR-RISK-1 to FR-RISK-3.

Safety-critical rule (FR-RISK-3, NFR-REL-2): the verdict must default to
INSUFFICIENT_DATA — never SAFE — whenever any required contributing agent's
output is missing or unavailable. This is the single most important invariant
in the whole system; it must be covered by fixture-based unit tests (LLD §2.6
"independently unit-testable") before this module is considered done.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["SAFE", "CAUTION", "UNSAFE", "INSUFFICIENT_DATA"]


@dataclass
class RiskVerdict:
    verdict: Verdict
    rationale: str
    contributing_factors: list[str] = field(default_factory=list)
