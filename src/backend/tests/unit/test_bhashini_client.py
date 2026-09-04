"""
tests/unit/test_bhashini_client.py

Fixture-driven tests for BhashiniClient (Sarvam-AI-backed) — no real API
calls. Mocks the sarvam_client injected via BhashiniClient(sarvam_client=...),
same pattern as SynthesisAgent's tests.
"""
from unittest.mock import MagicMock

import pytest

from app.language.bhashini_client import (
    BhashiniClient,
    BhashiniUnavailableError,
)


def _mock_stt_response(transcript: str, language_code: str, probability: float) -> MagicMock:
    resp = MagicMock()
    resp.transcript = transcript
    resp.language_code = language_code
    resp.language_probability = probability
    return resp


def _mock_tts_response(audios: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.audios = audios
    return resp


def _mock_lid_response(language_code: str) -> MagicMock:
    resp = MagicMock()
    resp.language_code = language_code
    return resp


# --------------------------------------------------------------------- #
# transcribe() — FR-LANG-1 / FR-LANG-2
# --------------------------------------------------------------------- #

def test_transcribe_returns_transcript_result():
    fake_sarvam = MagicMock()
    fake_sarvam.speech_to_text.transcribe.return_value = _mock_stt_response(
        "hello there", "en-IN", 0.95
    )

    client = BhashiniClient(sarvam_client=fake_sarvam)
    result = client.transcribe(b"fake-audio-bytes")

    assert result.text == "hello there"
    assert result.language_code == "en-IN"
    assert result.confidence == 0.95

    _, kwargs = fake_sarvam.speech_to_text.transcribe.call_args
    assert kwargs["language_code"] == "unknown"
    assert kwargs["mode"] == "transcribe"


def test_transcribe_raises_bhashini_unavailable_on_sdk_error():
    fake_sarvam = MagicMock()
    fake_sarvam.speech_to_text.transcribe.side_effect = RuntimeError("network down")

    client = BhashiniClient(sarvam_client=fake_sarvam)

    with pytest.raises(BhashiniUnavailableError):
        client.transcribe(b"fake-audio-bytes")


# --------------------------------------------------------------------- #
# synthesize() — FR-LANG-5 (via SynthesisAgent's caller)
# --------------------------------------------------------------------- #

def test_synthesize_decodes_first_audio_chunk():
    import base64

    fake_sarvam = MagicMock()
    raw_audio = b"\x00\x01\x02fake-wav-bytes"
    fake_sarvam.text_to_speech.convert.return_value = _mock_tts_response(
        [base64.b64encode(raw_audio).decode()]
    )

    client = BhashiniClient(sarvam_client=fake_sarvam)
    result = client.synthesize("Hello", "en-IN")

    assert result == raw_audio

    _, kwargs = fake_sarvam.text_to_speech.convert.call_args
    assert kwargs["speaker"] == "shubh"
    assert kwargs["model"] == "bulbul:v3"
    assert kwargs["language_code"] == "en-IN"


def test_synthesize_raises_when_no_audio_chunks_returned():
    fake_sarvam = MagicMock()
    fake_sarvam.text_to_speech.convert.return_value = _mock_tts_response([])

    client = BhashiniClient(sarvam_client=fake_sarvam)

    with pytest.raises(BhashiniUnavailableError):
        client.synthesize("Hello", "en-IN")


def test_synthesize_raises_bhashini_unavailable_on_sdk_error():
    fake_sarvam = MagicMock()
    fake_sarvam.text_to_speech.convert.side_effect = RuntimeError("rate limited")

    client = BhashiniClient(sarvam_client=fake_sarvam)

    with pytest.raises(BhashiniUnavailableError):
        client.synthesize("Hello", "en-IN")


def test_synthesize_warns_but_still_attempts_unsupported_language(caplog):
    import base64

    fake_sarvam = MagicMock()
    fake_sarvam.text_to_speech.convert.return_value = _mock_tts_response(
        [base64.b64encode(b"audio").decode()]
    )

    client = BhashiniClient(sarvam_client=fake_sarvam)
    with caplog.at_level("WARNING"):
        client.synthesize("Hello", "ta-IN")

    assert "not in supported set" in caplog.text
    fake_sarvam.text_to_speech.convert.assert_called_once()


# --------------------------------------------------------------------- #
# detect_language() — FR-LANG-2
# --------------------------------------------------------------------- #

def test_detect_language_returns_language_code():
    fake_sarvam = MagicMock()
    fake_sarvam.text.identify_language.return_value = _mock_lid_response("hi-IN")

    client = BhashiniClient(sarvam_client=fake_sarvam)
    result = client.detect_language("नमस्ते, आप कैसे हैं?")

    assert result == "hi-IN"


def test_detect_language_raises_bhashini_unavailable_on_sdk_error():
    fake_sarvam = MagicMock()
    fake_sarvam.text.identify_language.side_effect = RuntimeError("auth failed")

    client = BhashiniClient(sarvam_client=fake_sarvam)

    with pytest.raises(BhashiniUnavailableError):
        client.detect_language("some text")


# --------------------------------------------------------------------- #
# _build_client() — missing key path (FR-LANG-6's precondition)
# --------------------------------------------------------------------- #

def test_missing_api_key_raises_bhashini_unavailable(monkeypatch):
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("SARVAM_API_KEY", "")

    client = BhashiniClient()

    with pytest.raises(BhashiniUnavailableError, match="SARVAM_API_KEY is not set"):
        client.detect_language("test")

    get_settings.cache_clear()
