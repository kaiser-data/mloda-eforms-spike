"""Re-resolve every citation the reader emits with lxml (an independent XPath engine), and check coverage.

Reads the same documents as the mloda run (xml/ plus originals/), applies the same manifest check, and
fails on an empty or incomplete snapshot, on a populated field without its citation, or on any mismatch.
Usage: python -I src/check_citations.py DATA_DIR
"""
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).parent))
import eforms_lots as e

SAFE = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)  # untrusted input

# field -> (its citation column, how the cited text must relate to the row)
def _countries(r: dict) -> str:
    return ";".join(c for pair in (r["locations"] or "").split(";") for c in [pair.partition(":")[2]] if c)


# field -> (its citation column, how the cited text must relate to the row). Single-valued fields must
# select exactly one element; list fields (CPV, region, country) must select exactly the listed values.
LISTS = {"cpv", "nuts", "country"}
CHECKS = {
    "title": ("cite_title", lambda r, v: v == r["title"]),
    "cpv": ("cite_cpv", lambda r, v: v == r["cpv"]),
    "nuts": ("cite_nuts", lambda r, v: v == r["nuts"]),
    "country": ("cite_country", lambda r, v: v == _countries(r)),
    "deadline": ("cite_deadline", lambda r, v: v[:10] == r["deadline"]),
    "deadline_time": ("cite_deadline_time", lambda r, v: v == r["deadline_time"]),
    "request_deadline": ("cite_request_deadline", lambda r, v: v[:10] == r["request_deadline"]),
    "request_deadline_time": ("cite_request_deadline_time", lambda r, v: v == r["request_deadline_time"]),
    "value": ("cite_value", lambda r, v: float(v) == r["value"]),
}


def check(rows: list[dict]) -> tuple[int, list[str]]:
    """Returns (citations ok, problems). A populated field must have a citation that re-resolves to it."""
    ok, problems = 0, []
    for r in rows:
        for field, (col, same) in CHECKS.items():
            present = _countries(r) if field == "country" else r[field]
            if not present:
                continue  # a genuinely absent optional value needs no citation
            if not r[col]:
                problems.append(f"MISSING CITATION {r['notice']} {r['lot']} {field}")
                continue
            got = etree.parse(r["_path"], SAFE).xpath(r[col].split(" @ ")[1], namespaces=e.NS)
            if field in LISTS:
                val = ";".join(x.text.strip() for x in got if x.text and x.text.strip()) or None
            else:
                val = got[0].text.strip() if len(got) == 1 else None  # exactly one element
            if val is not None and same(r, val):
                ok += 1
            else:
                problems.append(f"MISMATCH {r[col]} -> {val!r}")
    return ok, problems


def main() -> None:
    folder = Path(sys.argv[1])
    missing = e.missing_from_manifest(folder)
    if missing:
        sys.exit(f"refused: {len(missing)} file(s) not downloaded or not in the manifest, e.g. {missing[0]}")
    rows, refused = e.read_folder(folder / "xml", folder / "fields.json", folder / "originals")
    if refused:
        sys.exit(f"refused: {refused[0]}")
    if not rows:
        sys.exit("refused: empty snapshot, nothing to check")
    ok, problems = check(rows)
    for p in problems:
        print(p)
    print(f"citations re-resolved: {ok} ok, {len(problems)} problems, "
          f"across {len(rows)} lots / {len({r['notice'] for r in rows})} notices")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
