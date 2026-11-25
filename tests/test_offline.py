"""Offline regression tests on a small synthetic original + correction (tests/fixtures/correction).

No TED download needed. The fixture is invented: no real buyer, no personal data.
"""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import check_citations
import eforms_lots
import eforms_mloda as m

FIX = ROOT / "tests" / "fixtures" / "correction"
CET = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 25, 11, 1, tzinfo=CET)
K = "tender_lots~"


def _run() -> pa.Table:
    [t] = m.mloda.run_all(
        features=[m.Feature(m.TENDER_LOTS)],
        compute_frameworks=[m.PyArrowTable],
        data_access_collection=m.DataAccessCollection(folders={str(FIX)}),
        plugin_collector=m.PluginCollector.enabled_feature_groups({m.EformsLotsReader}),
    )
    return t


def test_a_correction_changes_what_the_supplier_sees():
    t = _run()
    rows = {r[K + "notice"]: r for r in t.to_pylist()}
    assert rows["S-ORIGINAL"][K + "deadline"] == "2026-10-20"  # passed on 25 Nov
    assert rows["S-ORIGINAL"][K + "version_status"] == "superseded"
    hits = m.open_lots(t, NOW, "45", "DE", 30).to_pylist()
    assert len(hits) == 1
    hit = hits[0]
    assert hit[K + "notice"] == "S-CORRECTION" and hit[K + "action"] == "submit tender"
    assert hit[K + "until"] == "2026-11-27T11:00:00+01:00"
    assert hit[K + "cite_deadline_time"].startswith("S-CORRECTION LOT-0001 BT-131(t)-Lot @ ")


def test_empty_answer_keeps_columns_and_says_no_total():
    t = _run()
    empty = m.open_lots(t, datetime(2027, 6, 1, 12, 0, tzinfo=CET), "", "", 30)
    full = m.open_lots(t, NOW, "", "", 30)
    assert empty.num_rows == 0 and empty.schema.names == full.schema.names
    assert m.stated_total(empty) == "no total: none of the 0 lots states a value"


def test_cli_on_an_empty_window_succeeds():
    run = subprocess.run([sys.executable, "-I", str(ROOT / "src" / "eforms_mloda.py"), str(FIX),
                          "2027-06-01T12:00:00+01:00"], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    assert "0 lots" in run.stdout and "no total" in run.stdout


def test_checker_passes_the_fixture_and_fails_on_a_missing_citation():
    rows, refused = eforms_lots.read_folder(FIX / "xml", FIX / "fields.json", FIX / "originals")
    assert not refused
    ok, problems = check_citations.check(rows)
    assert ok == 6 and problems == []  # 2 notices x (date, time, value)
    rows[0]["cite_value"] = None  # keep the value, drop its citation
    _, problems = check_citations.check(rows)
    assert problems == [f"MISSING CITATION {rows[0]['notice']} LOT-0001 value"]


def test_checker_cli_passes_on_the_fixture():
    run = subprocess.run([sys.executable, "-I", str(ROOT / "src" / "check_citations.py"), str(FIX)],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0 and "6 ok, 0 problems" in run.stdout
