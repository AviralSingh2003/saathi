"""Show what Saathi has remembered (read-only; safe while the demo is running).

    python scripts/show_memory.py                  # everyone
    python scripts/show_memory.py --person meera
    python scripts/show_memory.py --auto           # only facts kept automatically
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from saathi import config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--person", help="person_id, e.g. meera")
    parser.add_argument("--auto", action="store_true", help="only memories saved by automatic capture")
    args = parser.parse_args()

    path = config.get().db_path
    if not path.exists():
        raise SystemExit(f"No database at {path}. Run the demo (or scripts/seed.py) first.")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(memory)")}
    if "source" not in columns:
        raise SystemExit("This database predates automatic memory. Restart the demo once to upgrade it.")

    sql, params = "SELECT * FROM memory WHERE 1 = 1", []
    if args.person:
        sql, params = sql + " AND person_id = ?", params + [args.person]
    if args.auto:
        sql += " AND source = 'auto'"
    rows = conn.execute(sql + " ORDER BY person_id, ts, memory_id", params).fetchall()
    conn.close()

    print(f"Database: {path}\n")
    if not rows:
        print("No memories match.")
        return
    current = None
    for row in rows:
        if row["person_id"] != current:
            current = row["person_id"]
            print(current)
        print(f"  #{row['memory_id']:<3} {row['ts'][:16].replace('T', ' ')}  {row['source']:<8} "
              f"{row['category']:<10} {row['fact']}")


if __name__ == "__main__":
    main()
