"""
Guards that the container image actually contains the runtime data files the
app reads at runtime.

Owner: P1 (Backend/Orchestration Lead). Issue #107.
Requirements: NFR-REL-1, FR-RISK-3 (indirectly — a missing boundary file makes
every verdict INSUFFICIENT_DATA), FR-GEO-1, FR-OCEAN-4.

WHY A TEST AND NOT JUST THE DOCKERFILE FIX: the Dockerfile copied only `app/`,
so `data/gis/imbl_mpa_boundaries.geojson` and `data/snapshots/pfz_latest.json.gz`
were absent from the deployed image. Nothing caught it — every test, and every
local run, reads those files straight from the working tree, so the failure
existed *only* in the deployed container and showed up as "the geofence agent
is permanently unavailable in production" days later.

This checks the property that actually matters: every filesystem path the
settings defaults point at is under something the Dockerfile COPYs. It's a
static check on purpose — it needs no Docker daemon, so it runs in ordinary
CI rather than only in a job someone might skip.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND_DIR / "Dockerfile"

# Settings fields whose value is a path read from disk at runtime. Add to this
# list when a new one appears — that is the point of the test.
RUNTIME_PATH_FIELDS = ("gis_boundary_data_path", "incois_pfz_snapshot_path")


def _copied_destinations() -> list[str]:
    """Destination paths from the Dockerfile's COPY instructions, normalised
    relative to WORKDIR. Deliberately simple parsing — this file is 15 lines
    and hand-maintained; a full Dockerfile parser would be more code than the
    thing it checks."""
    destinations = []
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.upper().startswith("COPY "):
            continue
        parts = line.split()[1:]
        if len(parts) < 2:
            continue
        destinations.append(parts[-1].lstrip("./").rstrip("/"))
    return destinations


@pytest.mark.parametrize("field", RUNTIME_PATH_FIELDS)
def test_runtime_data_paths_are_copied_into_the_image(field: str) -> None:
    configured = getattr(Settings(), field)
    relative = Path(configured.lstrip("./"))

    # The file has to exist in the repo at all...
    assert (BACKEND_DIR / relative).exists(), (
        f"Settings.{field} points at {configured}, which does not exist in the repo"
    )

    # ...and be under something the image copies, or it won't exist at runtime.
    copied = _copied_destinations()
    top_level = relative.parts[0]
    assert top_level in copied, (
        f"Settings.{field} = {configured}, but the Dockerfile never COPYs "
        f"{top_level!r} (it copies {copied}). The image would be missing this "
        "file, which only ever fails in the deployed container — see #107."
    )
