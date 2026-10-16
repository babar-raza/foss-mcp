"""`gatectl.run` must return on timeout even when a grandchild holds the output pipes.

Real incident (2026-10-16): a check whose test looped a shell forever hung a review for 3h25m,
because subprocess.run killed only the direct child on timeout and the orphaned grandchild kept
the captured pipes open, so communicate() never returned.
"""

from __future__ import annotations

import sys
import time

import gatectl as G


def test_timeout_returns_even_when_a_grandchild_holds_the_pipes() -> None:
    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "time.sleep(120)\n"
    )
    started = time.monotonic()
    rc, _out, err = G.run([sys.executable, "-c", script], timeout=3)
    elapsed = time.monotonic() - started
    assert rc == 124
    assert "TIMEOUT" in err
    assert elapsed < 40, f"run() took {elapsed:.0f}s to give up on a timed-out tree"


def test_normal_command_still_returns_output_and_code() -> None:
    rc, out, err = G.run(
        [sys.executable, "-c", "import sys; print('hi'); sys.stderr.write('e'); sys.exit(3)"]
    )
    assert (rc, out.strip(), err.strip()) == (3, "hi", "e")
