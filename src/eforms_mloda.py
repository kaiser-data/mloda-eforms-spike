"""mloda layer of the spike: `tender_lots` as a SourceFeatureGroup on mloda 0.22.0.

Mirrors the German Ledger reader pattern (mloda-registry, german_ledger/reader.py):
a folder in the DataAccessCollection is claimed when it holds eForms notices plus the SDK
fields.json the XPaths come from; the feature's subcolumns are `tender_lots~<column>`.

Run: .venv/bin/python -I src/eforms_mloda.py <folder with xml/ and fields.json> [today]
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
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


def open_lots(t: pa.Table, today: date, cpv: str, nuts: str, days: int) -> pa.Table:
    col = lambda c: t.column(f"{TENDER_LOTS}~{c}")
    deadline = col("deadline")  # ISO YYYY-MM-DD strings compare correctly as text
    mask = pc.and_(
        pc.and_(pc.greater_equal(deadline, today.isoformat()),
                pc.less_equal(deadline, (today + timedelta(days=days)).isoformat())),
        # superseded and conflict rows never answer; unresolved = newest version known, so it stays
        pc.invert(pc.is_in(col("version_status"), pa.array(["superseded", "conflict"]))),
    )
    if cpv:
        mask = pc.and_(mask, pc.match_substring_regex(col("cpv"), f"(^|;){cpv}"))
    if nuts:
        mask = pc.and_(mask, pc.match_substring_regex(col("nuts"), f"(^|;){nuts}"))
    return t.filter(pc.fill_null(mask, False))


def stated_total(t: pa.Table) -> str:
    pairs = list(zip(t.column(f"{TENDER_LOTS}~value").to_pylist(), t.column(f"{TENDER_LOTS}~currency").to_pylist()))
    if any(v is not None and not c for v, c in pairs):
        raise ValueError("refused: a stated value has no currency")
    cur = [c for v, c in pairs if v is not None]
    if len(set(cur)) > 1:
        raise ValueError(f"refused: mixed currencies {sorted(set(cur))}")
    vals = [v for v in t.column(f"{TENDER_LOTS}~value").to_pylist() if v is not None]
    if not vals:
        return f"no total: none of the {t.num_rows} lots states a value"
    return f"{sum(vals):,.0f} {cur[0] if cur else ''} over {len(vals)} of {t.num_rows} lots (the other {t.num_rows - len(vals)} state no value and are not counted as 0)"


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
    today = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    [table] = mloda.run_all(
        features=[Feature(TENDER_LOTS)],
        compute_frameworks=[PyArrowTable],
        data_access_collection=DataAccessCollection(folders={folder}),
        plugin_collector=PluginCollector.enabled_feature_groups({EformsLotsReader}),
    )
    status = pc.value_counts(table.column(f"{TENDER_LOTS}~version_status")).to_pylist()
    print(f"mloda run_all -> {table.num_rows} lot rows, {len(table.column_names)} columns; version status: "
          + ", ".join(f"{d['values']} {d['counts']}" for d in status))
    show_correction(table, today)
    for cpv, nuts, label in [("45", "DE2", "construction work, Bavaria"), ("", "DE", "any trade, a place of performance in Germany")]:
        hits = open_lots(table, today, cpv, nuts, 30)
        print(f"\nQ: open lots ({label}), deadline within 30 days of {today}: {hits.num_rows}")
        for r in hits.sort_by(f"{TENDER_LOTS}~deadline").slice(0, 4).to_pylist():
            g = lambda c, r=r: r[f"{TENDER_LOTS}~{c}"]
            val = f"{g('value'):,.0f} {g('currency')} [{g('cite_value')}]" if g("value") is not None else "value not stated"
            print(f"- {g('deadline')} {g('title')[:50]!r}\n    deadline [{g('cite_deadline')}]\n    value: {val}")
        print("  total:", stated_total(hits))


if __name__ == "__main__":
    main()
