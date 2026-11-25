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
import xml.etree.ElementTree as ET
from pathlib import Path

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
