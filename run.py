"""Development launcher; no package installation required."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from gekko_sync.ui import main

if __name__ == "__main__":
    main()
