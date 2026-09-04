"""
Shared scaffolding for the end-to-end integration suite (issue #36).

Owner: P1 (Backend/Orchestration Lead).
Reference: LLD v1.0 §6 (error-handling matrix), §2.2/§2.7; SRS NFR-PERF-2.

WHAT MAKES THIS "INTEGRATION" AND NOT tests/test_graph.py: test_graph.py
fakes every agent to exercise the graph's wiring. Everything here runs the
REAL PlannerAgent / WeatherAgent / OceanAgent / GeofencingAgent /
RiskSafetyAgent / SynthesisAgent, the REAL adapters, and the REAL Risk
decision tree. The only seam is the transport underneath them:

  * `fault_http` monkeypatches `httpx.get` (same router pattern as
    tests/test_adapter_faults.py), so the adapters' own parsing, timeout
    and status-mapping code all really runs.
  * `ScriptedLLM` stands in for the Gemini client so the offline rows are
    deterministic and key-free — it is a transport stub, not an agent fake:
    SynthesisAgent's citation-coverage and verdict-phrasing checks, and
    PlannerAgent's JSON parsing, still execute against its output.

The genuinely no-mocks-at-all run lives in test_live_e2e.py, which is
env-gated because it needs an LLM key and live external APIs.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest


# --------------------------------------------------------------------- #
# LLM transport stub
# --------------------------------------------------------------------- #
class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _Models:
    def __init__(self, owner: ScriptedLLM) -> None:
        self._owner = owner

    def generate_content(self, *, model: str, contents: str, config: dict) -> _Response:
        return self._owner._respond(model, contents)


class ScriptedLLM:
    """Minimal stand-in for `google.genai.Client` covering the one method both
    LLM-backed agents call. Routes on prompt content rather than on which
    agent called it, because that is the only thing the real client sees.

    `planner_payload` / `synthesis_payload` are the raw JSON dicts the model
    would return; `fail_with` (if set) is raised instead, which is how the
    LLD §6 "LLM provider timeout" row is exercised without waiting on a real
    timeout."""

    def __init__(
        self,
        planner_payload: dict[str, Any] | None = None,
        synthesis_payload: dict[str, Any] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.planner_payload = planner_payload
        self.synthesis_payload = synthesis_payload
        self.fail_with = fail_with
        self.calls: list[str] = []
        self.models = _Models(self)

    def _respond(self, model: str, contents: str) -> _Response:
        if self.fail_with is not None:
            raise self.fail_with
        if "entity-extraction step" in contents:
            self.calls.append("planner")
            return _Response(json.dumps(self.planner_payload or {}))
        self.calls.append("synthesis")
        return _Response(json.dumps(self.synthesis_payload or {"sentences": []}))


def planner_extraction(
    *,
    lat: float,
    lon: float,
    place_name: str = "Kochi",
    safety: bool = True,
    fishing: bool = True,
    boundary: bool = True,
    confidence: float = 0.95,
    time_window_text: str | None = None,
) -> dict[str, Any]:
    """The extraction payload a well-formed multi-intent query produces.
    Defaults fan out to all three specialists, which is what a "is it safe to
    fish near Kochi, and am I near any restricted zone?" query does."""
    return {
        "location_resolvable": True,
        "place_name": place_name,
        "lat": lat,
        "lon": lon,
        "time_window_text": time_window_text,
        "intent_safety": safety,
        "intent_fishing": fishing,
        "intent_boundary": boundary,
        "intent_keywords": ["safe", "fish", "zone"],
        "confidence": confidence,
    }


def sentences(*pairs: tuple[str, str]) -> dict[str, Any]:
    """`sentences(("Winds are light.", "weather"), ...)` -> the JSON shape
    SynthesisAgent._generate_sentences() parses."""
    return {"sentences": [{"text": text, "source": source} for text, source in pairs]}


# --------------------------------------------------------------------- #
# HTTP transport faults
# --------------------------------------------------------------------- #
def ok_json(body: Any):
    return lambda url: httpx.Response(200, json=body, request=httpx.Request("GET", url))


def ok_text(body: str):
    return lambda url: httpx.Response(200, text=body, request=httpx.Request("GET", url))


def raises(exc: Exception):
    def _behaviour(url):
        raise exc

    return _behaviour


CONNECT_ERROR = raises(httpx.ConnectError("stub: connection refused"))
READ_TIMEOUT = raises(httpx.ReadTimeout("stub: read timed out"))

# Healthy upstream bodies, keyed by the URL fragment that identifies each
# source. Values are deliberately calm (light wind, low waves, no alerts) so
# that any UNSAFE / INSUFFICIENT_DATA verdict in a test is attributable to the
# fault being injected, never to the weather itself.
GOOD_ROUTES: dict[str, Any] = {
    "marine-api": ok_json({"current": {"wave_height": 0.6, "time": 1788264000}}),
    "/forecast": ok_json(
        {
            "current": {
                "wind_speed_10m": 8.0,
                "precipitation": 0.0,
                "visibility": 20000.0,
                "time": 1788264000,
            }
        }
    ),
    "weatherapi": ok_json({"alerts": {"alert": []}}),
    "gdacs": ok_text("<rss><channel></channel></rss>"),
    # PFZ WFS: one advisory line well offshore of the test point.
    "PFZ_Automation": ok_json(
        {
            "features": [
                {
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [[[75.60, 9.90], [75.62, 9.95]]],
                    },
                    "properties": {"Year": 2026, "Julian_day": 247},
                }
            ]
        }
    ),
    "PFZ-TUNA-SST-CHL": ok_json({"features": [{"properties": {"GRAY_INDEX": 29.4}}]}),
}


@pytest.fixture()
def fault_http(monkeypatch: pytest.MonkeyPatch):
    """Returns `install(overrides=None, *, all=None)`: patches `httpx.get`
    with the healthy routes above, replacing any whose URL fragment appears in
    `overrides`, or every one of them if `all` is given.

    Fragments are matched as substrings of the request URL, longest first so a
    specific fragment wins over a general one. An unrouted URL is a hard
    failure rather than a silent pass-through — a live call leaking out of an
    offline test would make its result depend on the network."""

    def install(overrides: dict[str, Any] | None = None, *, all: Any = None) -> None:
        routes = dict.fromkeys(GOOD_ROUTES, all) if all is not None else dict(GOOD_ROUTES)
        routes.update(overrides or {})
        ordered = sorted(routes.items(), key=lambda kv: -len(kv[0]))

        def _get(url, **_kw):
            target = str(url)
            for fragment, behaviour in ordered:
                if fragment in target:
                    return behaviour(url)
            raise AssertionError(f"integration test made an unrouted HTTP call: {target}")

        monkeypatch.setattr(httpx, "get", _get)

    return install


@pytest.fixture(autouse=True)
def _clear_gis_boundary_cache():
    """GISBoundaryAdapter caches the parsed boundary GeoJSON at module scope
    (see its own `_CACHE` comment). Tests here point the adapter at a missing
    file to exercise the LLD §6 "GIS adapter failure" row, so the cache has to
    be cleared on both sides of every test — otherwise one test's faulted load
    (or its healthy load) leaks into the next."""
    from app.data_access import gis_boundary_adapter

    with gis_boundary_adapter._CACHE_LOCK:
        gis_boundary_adapter._CACHE = None
    yield
    with gis_boundary_adapter._CACHE_LOCK:
        gis_boundary_adapter._CACHE = None


@pytest.fixture()
def settings_override(monkeypatch: pytest.MonkeyPatch):
    """Overrides fields on the cached Settings object and restores the cache
    afterwards. `get_settings()` is `@lru_cache`d and adapters read it in
    `__init__`, so mutating the one cached instance is what actually reaches
    an adapter constructed inside the graph."""
    from app.core.config import get_settings

    def apply(**fields: Any) -> None:
        settings = get_settings()
        for name, value in fields.items():
            monkeypatch.setattr(settings, name, value)

    return apply
