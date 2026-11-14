"""TC-793 (G3/REQ-G3-017, H8): the serving image pins ``PYTHONHASHSEED``.

Without it every container start gets a fresh random hash seed, leaving any hash-order dependence live in
production. The pin sits in the ``runtime`` stage, which the ``demo`` stage derives from.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).parent.parent / "Dockerfile.serving"


def test_runtime_stage_pins_pythonhashseed() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^ENV PYTHONHASHSEED=0\s*$", text, re.MULTILINE), (
        "Dockerfile.serving has no ENV PYTHONHASHSEED=0"
    )


def test_pin_is_in_the_runtime_stage_directly_after_pythonpath() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(
        r"^ENV PYTHONPATH=/app/src\nENV PYTHONHASHSEED=0$", text.replace("\r\n", "\n"), re.MULTILINE
    )
    runtime_stage = text.split(" AS runtime", 1)[1].split("\nFROM ", 1)[0]
    assert "ENV PYTHONHASHSEED=0" in runtime_stage
