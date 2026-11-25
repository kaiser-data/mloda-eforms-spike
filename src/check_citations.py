"""Re-resolve every citation the reader emits with lxml (an independent XPath engine).

Reads the same documents as the mloda run (data/xml plus data/originals), applies the same manifest
check, and fails on an empty or incomplete snapshot or on any mismatch.
"""
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import eforms_lots as e

folder = Path(sys.argv[1])
missing = e.missing_from_manifest(folder)
if missing:
    sys.exit(f"refused: {len(missing)} file(s) not downloaded or not in the manifest, e.g. {missing[0]}")
rows, refused = e.read_folder(folder / "xml", folder / "fields.json", folder / "originals")
if refused:
    sys.exit(f"refused: {refused[0]}")
if not rows:
    sys.exit("refused: empty snapshot, nothing to check")

# (citation column, how the cited text must relate to the row)
CHECKS = [
    ("cite_deadline", lambda r, v: v[:10] == r["deadline"]),
    ("cite_deadline_time", lambda r, v: v == r["deadline_time"]),
    ("cite_request_deadline", lambda r, v: v[:10] == r["request_deadline"]),
    ("cite_request_deadline_time", lambda r, v: v == r["request_deadline_time"]),
    ("cite_value", lambda r, v: float(v) == r["value"]),
]
ok = bad = 0
for r in rows:
    for col, same in CHECKS:
        if r[col] is None:
            continue
        got = etree.parse(r["_path"]).xpath(r[col].split(" @ ")[1], namespaces=e.NS)  # the row's own file
        val = got[0].text.strip() if len(got) == 1 else None  # the XPath must select exactly one element
        match = val is not None and same(r, val)
        ok += match
        bad += not match
        if not match:
            print("MISMATCH", r[col], val)
print(f"citations re-resolved: {ok} ok, {bad} mismatched, across {len(rows)} lots / {len({r['notice'] for r in rows})} notices")
if bad:
    sys.exit(1)
