"""Print the full source of a pipeline module for review."""

import sys
from pathlib import Path

path = Path(sys.argv[1])
print(path.read_text(encoding="utf-8"))
