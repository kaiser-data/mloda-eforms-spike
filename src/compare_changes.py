"""What a change notice actually changes: compare each of the 34 with the notice it replaces.

Needs data/changes.json (src/resolve_changes.py) and the originals in data/originals/
(src/fetch_data.py fetches them). Lots are matched by lot id (BT-137).
Usage: python3 -I src/compare_changes.py DATA_DIR
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import eforms_lots

FIELDS = ["deadline", "value", "title", "cpv", "nuts"]


def main() -> None:
    data = Path(sys.argv[1])
    _, xpaths = eforms_lots.load_xpaths(data / "fields.json")
    pairs = json.loads((data / "changes.json").read_text())
    changed = {f: 0 for f in FIELDS}
    notices_with = {f: set() for f in FIELDS}
    lots_added = lots_removed = compared = 0
    examples = []
    for p in pairs:
        new = {r["lot"]: r for r in eforms_lots.read_notice(data / "xml" / f"{p['change_notice']}.xml", xpaths)}
        old = {r["lot"]: r for r in eforms_lots.read_notice(data / "originals" / f"{p['replaces']}.xml", xpaths)}
        lots_added += len(new.keys() - old.keys())
        lots_removed += len(old.keys() - new.keys())
        for lot in new.keys() & old.keys():
            compared += 1
            for f in FIELDS:
                if new[lot][f] != old[lot][f]:
                    changed[f] += 1
                    notices_with[f].add(p["change_notice"])
                    if f == "deadline" and len(examples) < 3:
                        examples.append(
                            f"{p['replaces']} -> {p['change_notice']} {lot}: deadline "
                            f"{old[lot]['deadline']} -> {new[lot]['deadline']}\n"
                            f"    old [{old[lot]['cite_deadline']}]\n    new [{new[lot]['cite_deadline']}]"
                        )
    print(f"{len(pairs)} change notices vs the notice each replaces; {compared} lots compared")
    for f in FIELDS:
        print(f"  {f:8s} changed on {changed[f]:3d} lots in {len(notices_with[f]):2d} of {len(pairs)} change notices")
    print(f"  lots added {lots_added}, lots removed {lots_removed}")
    print("examples:")
    for e in examples:
        print("  " + e)


if __name__ == "__main__":
    main()
