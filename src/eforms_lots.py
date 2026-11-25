"""Core of the eForms spike: SDK-driven lot rows where every value keeps its citation.

stdlib only. XPaths come from the official eForms SDK fields.json, not from this file.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET  # nosec B405 - TED notices; a real plugin would use defusedxml
from pathlib import Path

NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "efac": "http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1",
    "efbc": "http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1",
    "efext": "http://data.europa.eu/p27/eforms-ubl-extensions/1",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
}
LOT_PREFIX = "/*/cac:ProcurementProjectLot[cbc:ID/@schemeName='Lot']/"
LOT_FIELDS = ["BT-21-Lot", "BT-27-Lot", "BT-131(d)-Lot", "BT-262-Lot", "BT-5071-Lot"]
NOTICE_FIELDS = ["BT-04-notice", "BT-05(a)-notice", "BT-757-notice", "BT-758-notice", "BT-27-Procedure"]

COLUMNS = [
    "notice", "lot", "title", "deadline", "cpv", "nuts", "value", "currency", "value_scope",
    "procedure_id", "issue_date", "is_change_notice", "superseded_by",
    "cite_deadline", "cite_value", "sdk_version", "file_sha256",
]


class NotANotice(ValueError):
    """The file is not an eForms notice (e.g. an HTTP error page saved as .xml)."""


def load_xpaths(fields_json: Path) -> tuple[str, dict[str, str]]:
    sdk = json.loads(fields_json.read_text())
    fields = {f["id"]: f["xpathAbsolute"] for f in sdk["fields"]}
    return sdk["sdkVersion"], {fid: fields[fid] for fid in LOT_FIELDS + NOTICE_FIELDS}


def _find(node, xpath: str, prefix: str) -> list:
    rel = "./" + xpath.removeprefix(prefix)
    return [e for e in node.findall(rel, NS) if e.text and e.text.strip()]


def read_notice(path: Path, xpaths: dict[str, str]) -> list[dict]:
    raw = path.read_bytes()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise NotANotice(f"{path.name}: not XML ({e})") from e
    if not root.tag.startswith("{urn:oasis:names:specification:ubl:schema:xsd:"):
        raise NotANotice(f"{path.name}: root {root.tag} is not a UBL/eForms notice")
    sha = hashlib.sha256(raw).hexdigest()[:16]
    notice = path.stem
    meta = {fid: _find(root, xpaths[fid], "/*/") for fid in NOTICE_FIELDS}
    sdk = (root.findtext("cbc:CustomizationID", default="", namespaces=NS) or "").strip()

    lot_els = [
        lot for lot in root.findall("cac:ProcurementProjectLot", NS)
        if (i := lot.find("cbc:ID", NS)) is not None and i.get("schemeName") == "Lot"
    ]
    proc_value = meta["BT-27-Procedure"][0] if meta["BT-27-Procedure"] else None
    rows = []
    for idx, lot in enumerate(lot_els, start=1):
        lot_id = lot.find("cbc:ID", NS).text.strip()
        got = {fid: _find(lot, xpaths[fid], LOT_PREFIX) for fid in LOT_FIELDS}
        lot_path = f"{LOT_PREFIX.rstrip('/')}[{idx}]/"

        # Value rule: a lot's own BT-27; else the procedure BT-27 only when the notice has one lot;
        # else no value. A multi-lot procedure total is never split across lots.
        if got["BT-27-Lot"]:
            v, scope = got["BT-27-Lot"][0], "lot"
            cite_v = f"{notice} {lot_id} BT-27-Lot @ {lot_path}{xpaths['BT-27-Lot'].removeprefix(LOT_PREFIX)}"
        elif proc_value is not None and len(lot_els) == 1:
            v, scope = proc_value, "procedure (single lot)"
            cite_v = f"{notice} BT-27-Procedure @ {xpaths['BT-27-Procedure']}"
        else:
            v, scope, cite_v = None, "not stated", None

        dl = got["BT-131(d)-Lot"][0].text.strip() if got["BT-131(d)-Lot"] else None
        rows.append({
            "notice": notice,
            "lot": lot_id,
            "title": got["BT-21-Lot"][0].text.strip() if got["BT-21-Lot"] else None,
            "deadline": dl[:10] if dl else None,
            "cpv": ";".join(e.text.strip() for e in got["BT-262-Lot"]),
            "nuts": ";".join(e.text.strip() for e in got["BT-5071-Lot"]),
            "value": float(v.text) if v is not None else None,
            "currency": v.get("currencyID") if v is not None else None,
            "value_scope": scope,
            "procedure_id": meta["BT-04-notice"][0].text.strip() if meta["BT-04-notice"] else None,
            "issue_date": meta["BT-05(a)-notice"][0].text.strip()[:10] if meta["BT-05(a)-notice"] else None,
            "is_change_notice": bool(meta["BT-758-notice"]),
            "superseded_by": None,
            "cite_deadline": (
                f"{notice} {lot_id} BT-131(d)-Lot @ {lot_path}"
                f"{xpaths['BT-131(d)-Lot'].removeprefix(LOT_PREFIX)}" if dl else None
            ),
            "cite_value": cite_v,
            "sdk_version": sdk,
            "file_sha256": sha,
        })
    return rows


def mark_superseded(rows: list[dict]) -> None:
    """Within one procedure (BT-04), lots of older notices are superseded by the newest notice."""
    newest: dict[str, tuple[str, str]] = {}
    for r in rows:
        pid = r["procedure_id"]
        if pid and (pid not in newest or (r["issue_date"] or "", r["notice"]) > newest[pid]):
            newest[pid] = (r["issue_date"] or "", r["notice"])
    for r in rows:
        pid = r["procedure_id"]
        if pid and newest[pid][1] != r["notice"]:
            r["superseded_by"] = newest[pid][1]


def read_folder(folder: Path, fields_json: Path) -> tuple[list[dict], list[str]]:
    _, xpaths = load_xpaths(fields_json)
    rows, refused = [], []
    for p in sorted(folder.glob("*.xml")):
        try:
            rows += read_notice(p, xpaths)
        except NotANotice as e:
            refused.append(str(e))
    mark_superseded(rows)
    return rows, refused
