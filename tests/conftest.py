"""Test bootstrap for the src/ layout.

foss_mcp is not installed (no packaging metadata exists yet at the G0 gate),
so tests need src/ on sys.path to import it. Mirrors the same pattern used by
ops/tests/conftest.py for the ops/ package.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
