"""Re-resolve every citation the reader emits with lxml (an independent XPath engine)."""
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import eforms_lots as e

folder = Path(sys.argv[1])
rows, refused = e.read_folder(folder / "xml", folder / "fields.json")
ok = bad = 0
for r in rows:
    for cite, expect in [(r["cite_deadline"], r["deadline"]), (r["cite_value"], r["value"])]:
        if cite is None:
            continue
        notice, xpath = cite.split(" ")[0], cite.split(" @ ")[1]
        got = etree.parse(str(folder / "xml" / f"{notice}.xml")).xpath(xpath, namespaces=e.NS)
        val = got[0].text.strip() if got else None
        match = val is not None and (val[:10] == expect if isinstance(expect, str) else float(val) == expect)
        ok += match
        bad += not match
        if not match:
            print("MISMATCH", cite, val, expect)
print(f"citations re-resolved: {ok} ok, {bad} mismatched, across {len(rows)} lots / {len({r['notice'] for r in rows})} notices")
