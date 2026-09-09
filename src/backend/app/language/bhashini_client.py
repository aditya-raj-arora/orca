"""
Bhashini-compatible language client — ASR, TTS, and language-ID.

Owner: P2 (LLM/Synthesis Engineer).
Implements: FR-LANG-1, FR-LANG-2, FR-LANG-3, FR-LANG-6.
Reference: LLD v1.0 §2.1 (BhashiniClient interface).

PROVIDER SUBSTITUTION (flagged, not silent): this implements the exact
BhashiniClient interface from the LLD (transcribe/synthesize/detect_language,
same signatures and return types), but backs it with Sarvam AI's hosted API
instead of Bhashini/ULCA — government Bhashini sandbox access was blocked as
of testing on 2026-09-04 (see docs/CREDENTIALS.md #2 comment thread).
Sarvam AI (api.sarvam.ai, via the `sarvamai` PyPI SDK, confirmed v0.1.32)
provides ASR (Saaras), TTS (Bulbul), and language-ID (text-lid) as a single
hosted API with a free tier, covering the same three capabilities Bhashini
would have. Keeping this class named BhashiniClient and matching the LLD's
interface exactly means no other module (graph.py, main.py) needs to change
to consume it.

All three method signatures below (model, params, response field names) were
verified directly against the installed SDK (inspect.signature +
response.model_dump()) on 2026-09-04, not just Sarvam's docs — docs snippets
were found to be inconsistent with the actual SDK in places (e.g. `voice=`
vs. the SDK's real `speaker=` parameter; `meera` appearing in docs examples
but not present in bulbul:v3's actual speaker list).

SCOPED TO 2 LANGUAGES per docs/ORCA_Chat_Summary.docx §3 (Sprint 3 risk
mitigation for the compressed 6-day build) — see _SUPPORTED_LANGUAGES below.

If SARVAM_API_KEY is unset or the API is unreachable, transcribe()/
synthesize()/detect_language() raise BhashiniUnavailableError, caught
upstream (Gateway) to trigger the FR-LANG-6 fallback message in the
last-known language, else English.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Sprint 3 scope per Chat Summary §3: 2 languages only, expand later if time
# permits. Sarvam's BCP-47 codes for text-to-speech (bulbul:v3's supported
# set is a subset of what STT/LID support — confirmed via the TTS
# convert() signature, 2026-09-04).
_SUPPORTED_LANGUAGES = {"hi-IN", "en-IN"}

_STT_MODEL = "saaras:v3"
_TTS_MODEL = "bulbul:v3"
# bulbul:v3's actual default speaker per the installed SDK's help() text is
# "shubh" — NOT "meera" (meera appears only in older docs examples and is
# not in bulbul:v3's speaker list at all).
_DEFAULT_TTS_SPEAKER = "shubh"


class BhashiniUnavailableError(Exception):
    """Raised by transcribe()/synthesize()/detect_language() on any Sarvam
    API failure (network, auth, rate limit) — caught by the Gateway to
    trigger the FR-LANG-6 fallback message."""


@dataclass
class TranscriptResult:
    text: str
    language_code: str
    confidence: float


class BhashiniClient:
    """LLD §2.1 interface, Sarvam-AI-backed — see module docstring."""

    def __init__(self, sarvam_client: object | None = None) -> None:
        # Lazy construction, same pattern as PlannerAgent/SynthesisAgent —
        # unit tests never need a real API key.
        self._client = sarvam_client

    def transcribe(self, audio_bytes: bytes) -> TranscriptResult:
        """FR-LANG-1 / FR-LANG-2. Sends raw audio to Sarvam's speech-to-text
        endpoint with language_code="unknown" for auto-detection. Verified
        response fields (2026-09-04): .transcript, .language_code,
        .language_probability — no fabricated confidence value needed."""
        client = self._client or self._build_client()
        try:
            import io

            response = client.speech_to_text.transcribe(
                file=io.BytesIO(audio_bytes),
                model=_STT_MODEL,
                mode="transcribe",
                language_code="unknown",
            )
        except Exception as exc:  # noqa: BLE001 - normalise all provider errors
            logger.error("BhashiniClient.transcribe: Sarvam STT failed: %s", exc)
            raise BhashiniUnavailableError(str(exc)) from exc

        return TranscriptResult(
            text=response.transcript,
            language_code=response.language_code,
            confidence=response.language_probability,
        )

    def synthesize(self, text: str, language: str) -> bytes:
        """FR-LANG-5 (via Synthesis Agent's caller). Sends final response
        text to Sarvam's TTS endpoint. Verified response shape
        (2026-09-04): response.audios is a list of base64-encoded strings
        (docstring: "must be decoded before use") — returns the first
        chunk decoded to raw bytes."""
        if language not in _SUPPORTED_LANGUAGES:
            logger.warning(
                "BhashiniClient.synthesize: language %r not in supported "
                "set %r for this sprint — attempting anyway, Sarvam may "
                "still support it.", language, _SUPPORTED_LANGUAGES,
            )
        client = self._client or self._build_client()
        try:
            response = client.text_to_speech.convert(
                text=text,
                language_code=language,
                speaker=_DEFAULT_TTS_SPEAKER,
                model=_TTS_MODEL,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("BhashiniClient.synthesize: Sarvam TTS failed: %s", exc)
            raise BhashiniUnavailableError(str(exc)) from exc

        if not response.audios:
            raise BhashiniUnavailableError(
                "Sarvam TTS returned no audio chunks for the given text."
            )
        return base64.b64decode(response.audios[0])

    def detect_language(self, text: str) -> str:
        """FR-LANG-2. Used for typed-text queries where ASR isn't involved —
        still required so FR-LANG-2 holds for both input modes. Verified
        response field (2026-09-04): .language_code (e.g. "en-IN")."""
        client = self._client or self._build_client()
        try:
            response = client.text.identify_language(input=text)
        except Exception as exc:  # noqa: BLE001
            logger.error("BhashiniClient.detect_language: Sarvam LID failed: %s", exc)
            raise BhashiniUnavailableError(str(exc)) from exc
        return response.language_code

    def _build_client(self):
        settings = get_settings()
        if not getattr(settings, "sarvam_api_key", ""):
            raise BhashiniUnavailableError(
                "SARVAM_API_KEY is not set — see docs/CREDENTIALS.md #2"
            )

        from sarvamai import SarvamAI

        return SarvamAI(api_subscription_key=settings.sarvam_api_key)
