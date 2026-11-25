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
LOT_FIELDS = ["BT-21-Lot", "BT-27-Lot", "BT-131(d)-Lot", "BT-131(t)-Lot", "BT-1311(d)-Lot", "BT-1311(t)-Lot",
              "BT-262-Lot", "BT-5071-Lot", "BT-5141-Lot"]
# ISO 3166 alpha-3 (BT-5141 country) -> NUTS country prefix (BT-5071 region); NUTS uses EL for Greece, UK for GB
NUTS_PREFIX = {
    "AUT": "AT", "BEL": "BE", "BGR": "BG", "HRV": "HR", "CYP": "CY", "CZE": "CZ", "DNK": "DK", "EST": "EE",
    "FIN": "FI", "FRA": "FR", "DEU": "DE", "GRC": "EL", "HUN": "HU", "IRL": "IE", "ITA": "IT", "LVA": "LV",
    "LTU": "LT", "LUX": "LU", "MLT": "MT", "NLD": "NL", "POL": "PL", "PRT": "PT", "ROU": "RO", "SVK": "SK",
    "SVN": "SI", "ESP": "ES", "SWE": "SE", "NOR": "NO", "ISL": "IS", "LIE": "LI", "CHE": "CH", "GBR": "UK",
}
NOTICE_FIELDS = ["BT-04-notice", "BT-05(a)-notice", "BT-757-notice", "BT-758-notice", "BT-27-Procedure"]

COLUMNS = [
    "notice", "lot", "title", "deadline", "deadline_time", "request_deadline", "request_deadline_time",
    "cpv", "nuts", "locations", "location_conflict", "value", "currency", "value_scope",
    "procedure_id", "issue_date", "is_change_notice", "superseded_by", "version_status",
    "cite_deadline", "cite_deadline_time", "cite_request_deadline", "cite_request_deadline_time", "cite_value",
    "sdk_version", "file_sha256",
]


class NotANotice(ValueError):
    """The file is not an eForms notice (e.g. an HTTP error page saved as .xml)."""


def location_conflicts(locations: str | None) -> list[str]:
    """Addresses whose country (BT-5141) and NUTS region (BT-5071) name different countries."""
    out = []
    for pair in (locations or "").split(";"):
        nuts, _, country = pair.partition(":")
        if nuts and country and NUTS_PREFIX.get(country, nuts[:2]) != nuts[:2]:
            out.append(f"{nuts} vs {country}")
    return out


def located_in(locations: str | None, prefix: str) -> bool:
    """True when one address has a region under `prefix` and no country that contradicts it."""
    for pair in (locations or "").split(";"):
        nuts, _, country = pair.partition(":")
        if nuts.startswith(prefix) and (not country or NUTS_PREFIX.get(country, nuts[:2]) == nuts[:2]):
            return True
    return False


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
    # BT-701 notice identifier + version: what a later change notice names in its BT-758.
    nid = (root.findtext("cbc:ID[@schemeName='notice-id']", default="", namespaces=NS) or "").strip()
    ver = (root.findtext("cbc:VersionID", default="", namespaces=NS) or "").strip()

    lot_els = [
        lot for lot in root.findall("cac:ProcurementProjectLot", NS)
        if (i := lot.find("cbc:ID", NS)) is not None and i.get("schemeName") == "Lot"
    ]
    if not nid or not ver:
        raise NotANotice(f"{path.name}: no notice identifier (BT-701) or version")
    if not lot_els:
        raise NotANotice(f"{path.name}: notice {nid} has no procurement lots")
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

        def txt(fid: str, lot_got: dict = got) -> str | None:
            return lot_got[fid][0].text.strip() if lot_got[fid] else None

        def cite(fid: str, value: str | None, nid: str = notice, lid: str = lot_id, lp: str = lot_path) -> str | None:
            return f"{nid} {lid} {fid} @ {lp}{xpaths[fid].removeprefix(LOT_PREFIX)}" if value else None

        dl_t, rq, rq_t = txt("BT-131(t)-Lot"), txt("BT-1311(d)-Lot"), txt("BT-1311(t)-Lot")
        # pair country and region per address (the address path is the SDK's, minus the field)
        addr_rel = xpaths["BT-5071-Lot"].removeprefix(LOT_PREFIX).rsplit("/", 1)[0]
        locs = []
        for a in lot.findall("./" + addr_rel, NS):
            n = (a.findtext("cbc:CountrySubentityCode", default="", namespaces=NS) or "").strip()
            c = (a.findtext("cac:Country/cbc:IdentificationCode", default="", namespaces=NS) or "").strip()
            if n or c:
                locs.append(f"{n}:{c}")
        locations = ";".join(locs)
        rows.append({
            "notice": notice,
            "lot": lot_id,
            "title": got["BT-21-Lot"][0].text.strip() if got["BT-21-Lot"] else None,
            "deadline": dl[:10] if dl else None,
            "deadline_time": dl_t,
            "request_deadline": rq[:10] if rq else None,
            "request_deadline_time": rq_t,
            "cpv": ";".join(e.text.strip() for e in got["BT-262-Lot"]),
            "nuts": ";".join(e.text.strip() for e in got["BT-5071-Lot"]),
            "locations": locations,
            "location_conflict": "; ".join(location_conflicts(locations)) or None,
            "value": float(v.text) if v is not None else None,
            "currency": v.get("currencyID") if v is not None else None,
            "value_scope": scope,
            "procedure_id": meta["BT-04-notice"][0].text.strip() if meta["BT-04-notice"] else None,
            "issue_date": meta["BT-05(a)-notice"][0].text.strip()[:10] if meta["BT-05(a)-notice"] else None,
            "is_change_notice": bool(meta["BT-758-notice"]),
            "superseded_by": None,
            "version_status": None,
            "cite_deadline": (
                f"{notice} {lot_id} BT-131(d)-Lot @ {lot_path}"
                f"{xpaths['BT-131(d)-Lot'].removeprefix(LOT_PREFIX)}" if dl else None
            ),
            "cite_deadline_time": cite("BT-131(t)-Lot", dl_t),
            "cite_request_deadline": cite("BT-1311(d)-Lot", rq),
            "cite_request_deadline_time": cite("BT-1311(t)-Lot", rq_t),
            "cite_value": cite_v,
            "sdk_version": sdk,
            "file_sha256": sha,
            "_key": f"{nid}-{ver}" if nid and ver else None,
            "_path": str(path),
            "_sha_full": hashlib.sha256(raw).hexdigest(),
            "_replaces": meta["BT-758-notice"][0].text.strip() if meta["BT-758-notice"] else None,
        })
    return rows


def mark_superseded(rows: list[dict]) -> None:
    """Version status from exact BT-758 links only. No guessing from dates or procedure ids.

    BT-758 is the changed notice's identifier (BT-701) plus "-" and its version.
    - superseded: another loaded notice names this one in BT-758
    - conflict: two different loaded notices both claim to replace this one
    - unresolved: this is a change notice, but the notice it replaces isn't loaded
    - current_in_snapshot: nothing loaded replaces it (that is all a snapshot can say)
    """
    claims: dict[str, set[str]] = {}
    for r in rows:
        if r["_replaces"]:
            claims.setdefault(r["_replaces"], set()).add(r["notice"])
    loaded = {r["_key"] for r in rows}
    # two notices claiming the same original: the original and every claimant are in conflict
    in_conflict = {n for by in claims.values() if len(by) > 1 for n in by}
    for r in rows:
        by = claims.get(r["_key"], set())
        if len(by) > 1 or r["notice"] in in_conflict:
            r["version_status"] = "conflict"
        elif by:
            r["superseded_by"] = next(iter(by))
            r["version_status"] = "superseded"
        elif r["_replaces"] and r["_replaces"] not in loaded:
            r["version_status"] = "unresolved"
        else:
            r["version_status"] = "current_in_snapshot"
    # a conflict is carried down every later correction until something resolves the fork
    changed = True
    while changed:
        changed = False
        status = {r["_key"]: r["version_status"] for r in rows}
        for r in rows:
            if r["_replaces"] and status.get(r["_replaces"]) == "conflict" and r["version_status"] != "conflict":
                r["version_status"], changed = "conflict", True


def latest(rows: list[dict], row: dict) -> dict:
    """Follow superseded_by to the end of the chain (A -> B -> C gives C) for the same lot."""
    index = {(r["notice"], r["lot"]): r for r in rows}
    seen = set()
    while row["superseded_by"] and (row["notice"], row["lot"]) not in seen:
        seen.add((row["notice"], row["lot"]))
        nxt = index.get((row["superseded_by"], row["lot"]))
        if nxt is None:
            break
        row = nxt
    return row


def missing_from_manifest(folder: Path) -> list[str]:
    """Files that break the manifests (ids.txt for the sample, changes.json for originals): missing or unlisted."""
    missing = []
    if (folder / "ids.txt").exists():
        ids = [i for i in (folder / "ids.txt").read_text().split() if i.strip()]
        missing += [f"xml/{i}.xml" for i in ids if not (folder / "xml" / f"{i}.xml").exists()]
        missing += [f"xml/{p.name}: not in ids.txt" for p in (folder / "xml").glob("*.xml") if p.stem not in ids]
    if (folder / "changes.json").exists():
        reps = [c["replaces"] for c in json.loads((folder / "changes.json").read_text()) if c.get("replaces")]
        missing += [f"originals/{i}.xml" for i in reps if not (folder / "originals" / f"{i}.xml").exists()]
        if (folder / "originals").is_dir():
            missing += [f"originals/{p.name}: not in changes.json"
                        for p in (folder / "originals").glob("*.xml") if p.stem not in reps]
    return missing


def read_folder(folder: Path, fields_json: Path, originals: Path | None = None) -> tuple[list[dict], list[str]]:
    """Read every notice in `folder`, plus the replaced originals in `originals` if given.

    Each notice identity (BT-701 + version) is read once: an identical copy in both folders
    is skipped, and a different file with the same identity is refused as a conflict.
    """
    _, xpaths = load_xpaths(fields_json)
    rows, refused = [], []
    files = sorted(folder.glob("*.xml"))
    if originals is not None and originals.is_dir():
        files += sorted(originals.glob("*.xml"))
    seen: dict[str, tuple[str, str]] = {}
    for p in files:
        try:
            got = read_notice(p, xpaths)
        except NotANotice as e:
            refused.append(str(e))
            continue
        key = got[0]["_key"] if got else None
        if key:
            if key in seen:
                if seen[key][0] == got[0]["_sha_full"]:
                    continue  # the same notice twice: count it once
                refused.append(f"{p.name}: conflict, notice {key} also in {seen[key][1]} with different content")
                continue
            seen[key] = (got[0]["_sha_full"], p.name)
        rows += got
    mark_superseded(rows)
    return rows, refused
