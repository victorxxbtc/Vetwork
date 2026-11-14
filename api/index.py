"""Vercel Python function entrypoint: the same FastAPI app Railway runs, served as one ASGI function.

vercel.json rewrites every path here. VERCEL=1 is set by the platform, which switches the live
console from a long SSE stream to a one-request replay (/api/events) that fits function limits.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from swarm.web.app import app  # noqa: E402,F401
