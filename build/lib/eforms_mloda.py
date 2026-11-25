"""mloda layer of the spike: `tender_lots` as a SourceFeatureGroup on mloda 0.22.0.

Mirrors the German Ledger reader pattern (mloda-registry, german_ledger/reader.py):
a folder in the DataAccessCollection is claimed when it holds eForms notices plus the SDK
fields.json the XPaths come from; the feature's subcolumns are `tender_lots~<column>`.

Run: .venv/bin/python -I src/eforms_mloda.py <folder with xml/ and fields.json> [today]
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import pyarrow as pa
import pyarrow.compute as pc
from mloda.provider import ClaimRoute, FeatureSet, NamePolicy, SourceFeatureGroup, SourceMatch
from mloda.user import DataAccessCollection, Feature, Options, PluginCollector, mloda
from mloda_plugins.compute_framework.base_implementations.pyarrow.table import PyArrowTable

import eforms_lots

TENDER_LOTS = "tender_lots"


class EformsLotsReader(SourceFeatureGroup):
    """Claims a folder holding `fields.json` (eForms SDK) and `xml/*.xml` (TED notices)."""

    CLAIM_ROUTES = (ClaimRoute("folder", NamePolicy.DECLARED, True),)

    @classmethod
    def declared_names(cls) -> frozenset[str]:
        return frozenset({TENDER_LOTS})

    @classmethod
    def compute_framework_rule(cls) -> Any:
        return {PyArrowTable}

    @staticmethod
    def _is_dossier(path: str) -> bool:
        return os.path.isfile(os.path.join(path, "fields.json")) and os.path.isdir(os.path.join(path, "xml"))

    @classmethod
    def find_sources(cls, route: ClaimRoute, feature_name: str, options: Options,
                     data_access_collection: DataAccessCollection | None) -> list[SourceMatch]:
        folders = list(data_access_collection.folders.values()) if data_access_collection else []
        return [SourceMatch(source=os.path.abspath(f), access=f) for f in folders if cls._is_dossier(str(f))]

    @classmethod
    def columns(cls, match: SourceMatch) -> list[str]:
        return list(eforms_lots.COLUMNS)

    @classmethod
    def data_access_identity(cls, match: SourceMatch) -> str:
        return f"eforms:{os.path.basename(os.path.normpath(str(match.access)))}"

    @classmethod
    def load_neutral(cls, match: SourceMatch, features: FeatureSet) -> Any:
        folder = Path(str(match.access))
        missing = eforms_lots.missing_from_manifest(folder)
        if missing:  # refuse rather than answer from a partial download
            raise ValueError(f"refused: {len(missing)} file(s) not downloaded or not in the manifest, e.g. {missing[0]}")
        rows, refused = eforms_lots.read_folder(folder / "xml", folder / "fields.json", folder / "originals")
        if refused:  # refuse the whole run rather than answer from a partial or conflicting download
            raise ValueError(f"refused: {len(refused)} file(s), e.g. {refused[0]}")
        table = pa.Table.from_pylist(rows)
        return pa.table({f"{TENDER_LOTS}~{c}": table.column(c) for c in eforms_lots.COLUMNS})


TENDER, REQUEST = "submit tender", "request to participate"


def _deadline(day: str | None, time_: str | None, now: datetime) -> tuple[datetime | None, str]:
    """Exact instant when the notice gives a time; otherwise only the calendar day is known."""
    if not day:
        return None, "none"
    if not time_:
        return None, "date only"
    at = datetime.fromisoformat(f"{day}T{time_}")
    return (at if at.tzinfo else at.replace(tzinfo=now.tzinfo)), "exact"


def next_action(g: dict, now: datetime, days: int, stage: str | None = None) -> tuple[str, str, str] | None:
    """The earliest step a supplier can still take within `days` of `now`: (action, until, precision)."""
    end = now + timedelta(days=days)
    options = []
    for action, day, time_ in ((TENDER, g["deadline"], g["deadline_time"]),
                               (REQUEST, g["request_deadline"], g["request_deadline_time"])):
        if stage and action != stage:
            continue
        at, precision = _deadline(day, time_, now)
        if at is not None and now < at <= end:
            options.append((at.astimezone(UTC).isoformat(), action, at.isoformat(), precision))
        elif precision == "date only" and now.date().isoformat() <= day <= end.date().isoformat():
            options.append((f"{day}T23:59:59+00:00", action, f"{day} (date only, no time given)", precision))
    return min(options)[1:] if options else None


def open_lots(t: pa.Table, now: datetime, cpv: str, nuts: str, days: int, stage: str | None = None) -> pa.Table:
    """Lots a supplier can still act on at the instant `now`: submit a tender or request to participate.

    Superseded and conflict rows never answer (unresolved = newest version known, so it stays). A place
    filter needs one address whose region and country agree; contradicting addresses don't qualify a lot.
    """
    if now.tzinfo is None:
        raise ValueError("refused: the query time needs a time zone")
    k = f"{TENDER_LOTS}~"
    out = []
    for r in t.to_pylist():
        g = {c.removeprefix(k): v for c, v in r.items()}
        if g["version_status"] in ("superseded", "conflict"):
            continue
        if cpv and not any(c.startswith(cpv) for c in (g["cpv"] or "").split(";")):
            continue
        if nuts and not eforms_lots.located_in(g["locations"], nuts):
            continue
        act = next_action(g, now, days, stage)
        if act:
            out.append({**r, k + "action": act[0], k + "until": act[1], k + "until_precision": act[2]})
    if out:
        return pa.Table.from_pylist(out)
    empty = t.slice(0, 0)  # same columns and types as a non-empty answer
    for c in ("action", "until", "until_precision"):
        empty = empty.append_column(k + c, pa.array([], pa.string()))
    return empty


def stated_total(t: pa.Table) -> str:
    pairs = list(zip(t.column(f"{TENDER_LOTS}~value").to_pylist(), t.column(f"{TENDER_LOTS}~currency").to_pylist()))
    if any(v is not None and not c for v, c in pairs):
        raise ValueError("refused: a stated value has no currency")
    cur = [c for v, c in pairs if v is not None]
    if len(set(cur)) > 1:
        raise ValueError(f"refused: mixed currencies {sorted(set(cur))}")
    vals = [Decimal(str(v)) for v, _ in pairs if v is not None]
    if not vals:
        return f"no total: none of the {t.num_rows} lots states a value"
    return (f"{sum(vals):,.2f} {cur[0]} over {len(vals)} of {t.num_rows} lots "
            f"(the other {t.num_rows - len(vals)} state no value and are not counted as 0)")


def show_correction(t: pa.Table, today: date) -> None:
    """The original alone says the tender closed; the latest version in its chain says it is open."""
    k = f"{TENDER_LOTS}~"
    rows = [{c.removeprefix(k): v for c, v in r.items()} for r in t.to_pylist()]
    for r in rows:
        if r["superseded_by"] and r["deadline"] and r["deadline"] < today.isoformat():
            new = eforms_lots.latest(rows, r)
            if new is not r and new["version_status"] == "current_in_snapshot" and (new["deadline"] or "") >= today.isoformat():
                print(f"\nQ: is {r['notice']} {r['lot']} still open on {today}?")
                print(f"- original alone: deadline {r['deadline']}, looks closed  [{r['cite_deadline']}]")
                print(f"- latest version {new['notice']}: deadline {new['deadline']}, open  [{new['cite_deadline']}]")
                return


def main() -> None:
    folder = sys.argv[1]
    # an exact instant with a time zone, e.g. 2026-11-25T11:01:00+01:00
    now = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(UTC)
    [table] = mloda.run_all(
        features=[Feature(TENDER_LOTS)],
        compute_frameworks=[PyArrowTable],
        data_access_collection=DataAccessCollection(folders={folder}),
        plugin_collector=PluginCollector.enabled_feature_groups({EformsLotsReader}),
    )
    status = pc.value_counts(table.column(f"{TENDER_LOTS}~version_status")).to_pylist()
    print(f"mloda run_all -> {table.num_rows} lot rows, {len(table.column_names)} columns; version status: "
          + ", ".join(f"{d['values']} {d['counts']}" for d in status))
    conflicts = [r for r in table.to_pylist() if r[f"{TENDER_LOTS}~location_conflict"]]
    print(f"location conflicts (country vs region in the same address): {len(conflicts)} lots, e.g. "
          + (f"{conflicts[0][f'{TENDER_LOTS}~notice']} {conflicts[0][f'{TENDER_LOTS}~location_conflict']}" if conflicts else "none"))
    show_correction(table, now.date())
    for cpv, nuts, label in [("45", "DE2", "construction work, Bavaria"), ("", "DE", "any trade, a place of performance in Germany")]:
        hits = open_lots(table, now, cpv, nuts, 30)
        acts = pc.value_counts(hits.column(f"{TENDER_LOTS}~action")).to_pylist() if hits.num_rows else []
        print(f"\nQ: what can a supplier still do ({label}) between {now.isoformat()} and 30 days later? {hits.num_rows} lots: "
              + ", ".join(f"{d['values']} {d['counts']}" for d in acts))
        for r in hits.sort_by(f"{TENDER_LOTS}~until").slice(0, 4).to_pylist():
            g = lambda c, r=r: r[f"{TENDER_LOTS}~{c}"]
            val = f"{g('value'):,.2f} {g('currency')} [{g('cite_value')}]" if g("value") is not None else "value not stated"
            dl = g("cite_deadline_time") or g("cite_deadline") if g("action") == TENDER else g("cite_request_deadline_time") or g("cite_request_deadline")
            print(f"- {g('action')} until {g('until')}: {g('title')[:50]!r}\n    deadline [{dl}]\n    value: {val}")
        print("  total:", stated_total(hits))


if __name__ == "__main__":
    main()
