"""
BhashiniClient — wraps ASR, language identification, and TTS behind one
internal interface.

Owner: P2 (LLM/Synthesis Engineer), with P5 (Frontend Core UI) as the primary
consumer/tester since voice UX has a two-way dependency (Chat Summary §4:
"Bhashini and the voice UI also have a two-way dependency since real audio
behavior is harder to mock than JSON").

Implements: FR-LANG-1 to FR-LANG-6.
Reference: LLD v1.0 §2.1.

DESIGN RULE (do not violate): BhashiniClient is the ONLY module permitted to
import the Bhashini SDK/HTTP client directly (LLD §2.1 design note) — no agent
or gateway code should call Bhashini's REST API directly. Route everything
through here.

SRS RISK-2 mitigation: validate this against 2-3 languages first (per the
6-day plan in docs/ORCA_Chat_Summary.docx, Bhashini is scoped to 2 languages
for this build), then expand once the core pipeline is proven.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


class BhashiniUnavailableError(Exception):
    """Raised on any Bhashini API failure. Caught by the Gateway (app/main.py)
    to trigger the FR-LANG-6 user-visible fallback message — do not swallow
    this silently anywhere else."""


@dataclass
class TranscriptResult:
    text: str
    language_code: str
    confidence: float


class BhashiniClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.bhashini_api_key
        self._user_id = settings.bhashini_user_id
        self._base_url = settings.bhashini_base_url

    def transcribe(self, audio_bytes: bytes) -> TranscriptResult:
        """Sends raw audio to the Bhashini ASR endpoint.

        TODO(P2): implement; raise BhashiniUnavailableError on failure rather
        than returning a partial/garbage TranscriptResult.
        """
        raise NotImplementedError

    def synthesize(self, text: str, language: str) -> bytes:
        """Sends final response text + target language to Bhashini TTS.
        Returns playable audio bytes.

        TODO(P2): implement.
        """
        raise NotImplementedError

    def detect_language(self, text: str) -> str:
        """Used for typed-text queries where ASR is not involved — still
        required so FR-LANG-2 holds for both voice and text input modes.

        TODO(P2): implement.
        """
        raise NotImplementedError
