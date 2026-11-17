#!/usr/bin/env python3
"""folderIntelligence - avvio senza installazione: `python3 __main__.py ~/Downloads`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from folder_intelligence.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
