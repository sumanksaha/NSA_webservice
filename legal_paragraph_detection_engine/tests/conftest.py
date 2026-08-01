"""Pytest bootstrap for the engine (T-42).

When tests are run from *inside* ``legal_paragraph_detection_engine/`` the
repository root is not on ``sys.path``, so ``import legal_paragraph_detection_engine``
would fail. This conftest adds the repository root so the package resolves
regardless of where pytest is invoked from.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
