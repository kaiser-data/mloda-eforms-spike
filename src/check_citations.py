"""Re-resolve every citation the reader emits with lxml (an independent XPath engine).

Reads the same documents as the mloda run: data/xml plus data/originals.
"""
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import eforms_lots as e

folder = Path(sys.argv[1])
rows, refused = e.read_folder(folder / "xml", folder / "fields.json", folder / "originals")
if refused:
    sys.exit(f"refused: {refused[0]}")
ok = bad = 0
for r in rows:
    for cite, expect in [(r["cite_deadline"], r["deadline"]), (r["cite_value"], r["value"])]:
        if cite is None:
            continue
        xpath = cite.split(" @ ")[1]
        got = etree.parse(r["_path"]).xpath(xpath, namespaces=e.NS)  # the file this row was read from
        val = got[0].text.strip() if len(got) == 1 else None  # the XPath must select exactly one element
        match = val is not None and (val[:10] == expect if isinstance(expect, str) else float(val) == expect)
        ok += match
        bad += not match
        if not match:
            print("MISMATCH", cite, val, expect)
print(f"citations re-resolved: {ok} ok, {bad} mismatched, across {len(rows)} lots / {len({r['notice'] for r in rows})} notices")
if bad:
    sys.exit(1)
