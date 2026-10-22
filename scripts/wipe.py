"""Delete everything Saathi stores about a person.

    python scripts/wipe.py --person meera
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saathi.core import SaathiError  # noqa: E402
from saathi.core.people import wipe_person  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--person", required=True, help="person_id, e.g. meera")
    args = parser.parse_args()
    try:
        result = wipe_person(args.person)
    except SaathiError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Deleted all data for {result['person_id']}: {result['deleted']}")


if __name__ == "__main__":
    main()
