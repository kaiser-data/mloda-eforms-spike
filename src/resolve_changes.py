"""Resolve each change notice (BT-758) to the TED notice it replaces.

BT-758 holds the changed notice's identifier (BT-701) plus "-" and its two-digit
version. TED's search API is queried by procedure identifier (BT-04); the notice whose
identifier and version match BT-758 is the one being replaced.

Usage: python3 -I src/resolve_changes.py DATA_DIR   (writes DATA_DIR/changes.json)
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import defusedxml.ElementTree as ET

API = "https://api.ted.europa.eu/v3/notices/search"
NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "efbc": "http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1",
}
FIELDS = ["publication-number", "notice-identifier", "notice-version", "publication-date"]


def search(procedure_id: str) -> list[dict]:
    body = json.dumps({"query": f'procedure-identifier = "{procedure_id}"', "fields": FIELDS, "limit": 100}).encode()
    for attempt in range(4):
        req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)["notices"]
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"TED kept rate-limiting procedure {procedure_id}")


def resolve(data: Path) -> list[dict]:
    """One level, through the TED search API (how the committed data/changes.json was made)."""
    rows = []
    for f in sorted((data / "xml").glob("*.xml")):
        root = ET.parse(f).getroot()
        changed = root.findtext(".//efbc:ChangedNoticeIdentifier", namespaces=NS)
        if not changed:
            continue
        proc = root.findtext("cbc:ContractFolderID", namespaces=NS)
        target_id, _, target_ver = changed.strip().rpartition("-")
        hits = search(proc.strip())
        match = [n for n in hits if n["notice-identifier"] == target_id and int(n["notice-version"]) == int(target_ver)]
        rows.append({
            "change_notice": f.stem,
            "procedure_id": proc.strip(),
            "bt_758": changed.strip(),
            "replaces": match[0]["publication-number"] if len(match) == 1 else None,
            "replaced_published": match[0]["publication-date"][:10] if len(match) == 1 else None,
            "notices_in_procedure": len(hits),
            "status": "resolved" if len(match) == 1 else ("ambiguous" if match else "not_found"),
        })
        time.sleep(1)
    return rows


def _identity(raw: bytes) -> tuple[str | None, str | None, str | None]:
    """(own key = BT-701 + "-" + version, BT-758 it replaces, procedure id) of one notice."""
    root = ET.fromstring(raw)
    nid = (root.findtext("cbc:ID[@schemeName='notice-id']", default="", namespaces=NS) or "").strip()
    ver = (root.findtext("cbc:VersionID", default="", namespaces=NS) or "").strip()
    rep = (root.findtext(".//efbc:ChangedNoticeIdentifier", default="", namespaces=NS) or "").strip()
    proc = (root.findtext("cbc:ContractFolderID", default="", namespaces=NS) or "").strip()
    return (f"{nid}-{ver}" if nid and ver else None), (rep or None), (proc or None)


def archive_lookup(archive: Path):
    """A lookup over a local folder of notices (offline tests, or a mirror of earlier fetches)."""
    index = {}
    for p in sorted(archive.glob("*.xml")):
        key, _, _ = _identity(p.read_bytes())
        if key:
            index[key] = (p.stem, p.read_bytes())
    return lambda procedure_id, key: index.get(key)


def resolve_chains(data: Path, lookup, max_depth: int = 50) -> list[dict]:
    """Follow BT-758 from every notice in data/xml back through all predecessors.

    `lookup(procedure_id, key)` returns (name, xml bytes) or None. Found predecessors are saved to
    data/originals/; each link is a row in data/changes.json. Missing links are reported, cycles refused.
    Running it again on the same data gives the same result.
    """
    (data / "originals").mkdir(exist_ok=True)
    known = {}  # key -> name, for everything already on disk
    todo = []
    for folder in (data / "xml", data / "originals"):
        for p in sorted(folder.glob("*.xml")):
            key, rep, proc = _identity(p.read_bytes())
            if key:
                known[key] = p.stem
            if rep:
                todo.append((p.stem, rep, proc, 0))
    rows, seen = [], set()
    while todo:
        name, rep, proc, depth = todo.pop(0)
        if (name, rep) in seen:
            continue
        seen.add((name, rep))
        if depth > max_depth:
            raise RuntimeError(f"refused: correction chain from {name} is longer than {max_depth} (cycle?)")
        if rep in known:
            target = known[rep]
            status = "resolved"
        else:
            hit = lookup(proc, rep)
            if hit is None:
                rows.append({"change_notice": name, "bt_758": rep, "replaces": None, "status": "not_found"})
                continue
            target, raw = hit
            (data / "originals" / f"{target}.xml").write_bytes(raw)
            known[rep] = target
            status = "resolved"
            _, nxt, nproc = _identity(raw)
            if nxt:
                todo.append((target, nxt, nproc, depth + 1))
        if target == name:
            raise RuntimeError(f"refused: {name} names itself in BT-758")
        rows.append({"change_notice": name, "bt_758": rep, "replaces": target, "status": status})
    rows.sort(key=lambda r: (r["change_notice"], r["bt_758"]))
    (data / "changes.json").write_text(json.dumps(rows, indent=1))
    return rows


def main() -> None:
    data = Path(sys.argv[1])
    rows = resolve(data)
    (data / "changes.json").write_text(json.dumps(rows, indent=1))
    ok = sum(r["status"] == "resolved" for r in rows)
    print(f"change notices: {len(rows)}, resolved to the notice they replace: {ok}")
    for r in rows:
        if r["status"] != "resolved":
            print(f"  {r['change_notice']}: {r['status']} (BT-758 {r['bt_758']})")


if __name__ == "__main__":
    main()
