import sys
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))
