"""
scripts/verify_llm_key.py

One-off script for Issue #4 acceptance criterion 1: confirm LLM_API_KEY works
against a real sample call before anyone builds against it.

Run from anywhere: python scripts/verify_llm_key.py
(This script lives outside src/backend, so it adds src/backend to sys.path
itself rather than assuming the caller's cwd or PYTHONPATH is set up.)
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/verify_llm_key.py -> repo root -> src/backend
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _REPO_ROOT / "src" / "backend"

if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from app.core.config import get_settings  # noqa: E402 - must follow sys.path fix


def main() -> int:
    settings = get_settings()

    if not settings.llm_api_key:
        print("FAIL: LLM_API_KEY is empty. Set it in src/backend/.env — see docs/CREDENTIALS.md.")
        return 1

    if settings.llm_provider != "google":
        print(
            f"NOTE: llm_provider is '{settings.llm_provider}', not 'google'. "
            "This script only verifies the Google Gemini path. Update it if "
            "you've switched to the Groq backup (docs/CREDENTIALS.md)."
        )

    try:
        from google import genai
    except ImportError as exc:
        print(f"FAIL: google-genai not installed ({exc}). Run: pip install google-genai")
        return 1

    client = genai.Client(api_key=settings.llm_api_key)

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents="Reply with exactly the word: OK",
        )
    except Exception as exc:  # noqa: BLE001 - deliberate broad catch for a diagnostic script
        print(f"FAIL: API call raised an exception: {exc}")
        return 1

    print(f"Response text: {response.text!r}")
    print("PASS: LLM_API_KEY is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())