"""Reset the database and load the demo data (Meera, Raj, Priya).

    python scripts/seed.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saathi.seed import main  # noqa: E402

if __name__ == "__main__":
    main()
