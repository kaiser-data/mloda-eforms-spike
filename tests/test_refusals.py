import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import eforms_mloda as m

SAMPLE = ["800869-2026.xml", "800306-2026.xml"]
CET = timezone(timedelta(hours=1))
NOON = datetime(2026, 11, 25, 12, 0, tzinfo=CET)


def _dossier(tmp_path: Path) -> Path:
    (tmp_path / "xml").mkdir()
    shutil.copy(ROOT / "data" / "fields.json", tmp_path / "fields.json")
    for n in SAMPLE:
        shutil.copy(ROOT / "data" / "xml" / n, tmp_path / "xml" / n)
    return tmp_path


def _run(folder: Path) -> pa.Table:
    [t] = m.mloda.run_all(
        features=[m.Feature(m.TENDER_LOTS)],
        compute_frameworks=[m.PyArrowTable],
        data_access_collection=m.DataAccessCollection(folders={str(folder)}),
        plugin_collector=m.PluginCollector.enabled_feature_groups({m.EformsLotsReader}),
    )
    return t


def test_reads_real_notices_with_citations(tmp_path):
    t = _run(_dossier(tmp_path))
    rows = {r["tender_lots~notice"]: r for r in t.to_pylist()}
    assert rows["800869-2026"]["tender_lots~value"] == 723800.0
    assert "BT-27-Lot" in rows["800869-2026"]["tender_lots~cite_value"]
    assert rows["800306-2026"]["tender_lots~value_scope"] == "not stated"


def test_refuses_an_http_error_page_saved_as_xml(tmp_path):
    d = _dossier(tmp_path)
    (d / "xml" / "800999-2026.xml").write_text("<html><head><title>429 Too Many Requests</title></head></html>")
    with pytest.raises(Exception, match="not a UBL/eForms notice"):
        _run(d)


def test_refuses_to_sum_mixed_currencies(tmp_path):
    t = _run(_dossier(tmp_path))
    cur = t.column("tender_lots~currency").to_pylist()
    t = t.set_column(t.column_names.index("tender_lots~currency"), "tender_lots~currency",
                     pa.array(["PLN" if c else "EUR" for c in cur]))
    t = t.set_column(t.column_names.index("tender_lots~value"), "tender_lots~value", pa.array([1.0, 2.0]))
    with pytest.raises(ValueError, match="mixed currencies"):
        m.stated_total(t)


def test_gives_no_total_when_no_lot_states_a_value(tmp_path):
    t = _run(_dossier(tmp_path))
    t = t.set_column(t.column_names.index("tender_lots~value"), "tender_lots~value",
                     pa.array([None] * t.num_rows, pa.float64()))
    out = m.stated_total(t)
    assert out.startswith("no total")
    assert "0 " not in out.split(":")[0]


def test_change_notice_supersedes_exactly_the_notice_it_names(tmp_path):
    # real pair, resolved through BT-758: 692407-2026 is replaced by 800157-2026
    d = _dossier(tmp_path)
    shutil.copy(ROOT / "data" / "xml" / "800157-2026.xml", d / "xml")
    shutil.copy(ROOT / "data" / "originals" / "692407-2026.xml", d / "xml")
    rows = _run(d).to_pylist()
    old = [r for r in rows if r["tender_lots~notice"] == "692407-2026"]
    assert old and all(r["tender_lots~superseded_by"] == "800157-2026" for r in old)
    others = [r for r in rows if r["tender_lots~notice"] != "692407-2026"]
    assert all(r["tender_lots~superseded_by"] is None for r in others)


def test_one_run_loads_originals_and_applies_corrections(tmp_path):
    # data/originals/ is read in the same run_all; the replaced original's lots drop out of open_lots
    d = _dossier(tmp_path)
    shutil.copy(ROOT / "data" / "xml" / "800275-2026.xml", d / "xml")
    (d / "originals").mkdir()
    shutil.copy(ROOT / "data" / "originals" / "763684-2026.xml", d / "originals")
    t = _run(d)
    old = [r for r in t.to_pylist() if r["tender_lots~notice"] == "763684-2026"]
    assert old and all(r["tender_lots~superseded_by"] == "800275-2026" for r in old)
    hits = m.open_lots(t, NOON, "", "", 30).column("tender_lots~notice").to_pylist()
    assert "763684-2026" not in hits and "800275-2026" in hits


# --- version identity, chains and completeness -------------------------------------------

A, B = "692407-2026", "800157-2026"  # real pair: B's BT-758 names A; both deadlines fall in the window
TODAY = NOON


def _pair(tmp_path: Path) -> Path:
    d = _dossier(tmp_path)
    shutil.copy(ROOT / "data" / "xml" / f"{B}.xml", d / "xml")
    (d / "originals").mkdir()
    shutil.copy(ROOT / "data" / "originals" / f"{A}.xml", d / "originals")
    return d


def _notices(t: pa.Table) -> list[str]:
    return m.open_lots(t, TODAY, "", "", 30).column("tender_lots~notice").to_pylist()


def test_superseded_flag_alone_excludes_an_original_inside_the_window(tmp_path):
    t = _run(_pair(tmp_path))
    rows = {r["tender_lots~notice"]: r for r in t.to_pylist()}
    assert rows[A]["tender_lots~deadline"] == "2026-12-02"  # A would match the window on its own
    assert rows[A]["tender_lots~version_status"] == "superseded"
    assert A not in _notices(t) and B in _notices(t)


def test_same_notice_in_both_folders_is_counted_once(tmp_path):
    d = _pair(tmp_path)
    shutil.copy(ROOT / "data" / "xml" / f"{B}.xml", d / "originals")
    rows = [r for r in _run(d).to_pylist() if r["tender_lots~notice"] == B]
    assert len(rows) == 1


def test_same_identity_with_different_content_is_refused(tmp_path):
    d = _pair(tmp_path)
    raw = (ROOT / "data" / "xml" / f"{B}.xml").read_text(encoding="utf-8")
    (d / "originals" / "copy.xml").write_text(raw.replace("2026-12-21+01:00", "2026-12-24+01:00"), encoding="utf-8")
    with pytest.raises(Exception, match="conflict"):
        _run(d)


def test_chain_of_corrections_resolves_to_the_last_version(tmp_path):
    # synthetic C replaces the real B, which replaces the real A
    d = _pair(tmp_path)
    raw = (ROOT / "data" / "xml" / f"{B}.xml").read_text(encoding="utf-8")
    c = (raw.replace("9bcdb08c-aae7-41a4-96e7-f73bf14bc1d1", "00000000-0000-4000-8000-00000000000c")
            .replace("150ae3e7-899c-4c62-980e-9b01093470e9-01", "9bcdb08c-aae7-41a4-96e7-f73bf14bc1d1-01")
            .replace("2026-12-21+01:00", "2026-12-20+01:00"))
    (d / "xml" / "C-synthetic.xml").write_text(c, encoding="utf-8")
    t = _run(d)
    rows = [{k.removeprefix("tender_lots~"): v for k, v in r.items()} for r in t.to_pylist()]
    by = {r["notice"]: r for r in rows}
    assert by[A]["superseded_by"] == B and by[B]["superseded_by"] == "C-synthetic"
    assert m.eforms_lots.latest(rows, by[A])["deadline"] == "2026-12-20"
    assert [n for n in _notices(t) if n in (A, B, "C-synthetic")] == ["C-synthetic"]


def test_missing_download_from_manifest_is_refused(tmp_path):
    d = _pair(tmp_path)
    (d / "ids.txt").write_text(f"800869-2026\n800306-2026\n{B}\n800999-2026\n")
    with pytest.raises(Exception, match="not downloaded"):
        _run(d)


def test_two_corrections_of_one_original_are_a_conflict_and_never_answer(tmp_path):
    d = _pair(tmp_path)
    raw = (ROOT / "data" / "xml" / f"{B}.xml").read_text(encoding="utf-8")
    (d / "xml" / "B2-synthetic.xml").write_text(
        raw.replace("9bcdb08c-aae7-41a4-96e7-f73bf14bc1d1", "00000000-0000-4000-8000-0000000000b2"), encoding="utf-8")
    t = _run(d)
    status = {r["tender_lots~notice"]: r["tender_lots~version_status"] for r in t.to_pylist()}
    assert status[A] == status[B] == status["B2-synthetic"] == "conflict"
    assert not {A, B, "B2-synthetic"} & set(_notices(t))


def test_a_value_without_currency_is_refused_in_a_total(tmp_path):
    t = _run(_dossier(tmp_path))
    t = t.set_column(t.column_names.index("tender_lots~value"), "tender_lots~value", pa.array([100.0, 200.0]))
    t = t.set_column(t.column_names.index("tender_lots~currency"), "tender_lots~currency", pa.array(["EUR", None]))
    with pytest.raises(ValueError, match="no currency"):
        m.stated_total(t)


def test_a_file_not_in_the_manifest_is_refused(tmp_path):
    d = _pair(tmp_path)
    (d / "ids.txt").write_text(f"800869-2026\n800306-2026\n{B}\n")
    shutil.copy(ROOT / "data" / "xml" / "800157-2026.xml", d / "xml" / "800888-2026.xml")
    with pytest.raises(Exception, match="not in ids.txt"):
        _run(d)


def test_a_notice_without_lots_is_refused(tmp_path):
    d = _dossier(tmp_path)
    raw = (ROOT / "data" / "xml" / "800869-2026.xml").read_text(encoding="utf-8")
    (d / "xml" / "nolots.xml").write_text(raw.replace('schemeName="Lot"', 'schemeName="Part"'), encoding="utf-8")
    with pytest.raises(Exception, match="no procurement lots"):
        _run(d)


# --- what a supplier can still do, until exactly when (review acceptance cases) ---------------

def _full() -> pa.Table:
    return _run(ROOT / "data")


def test_lots_that_closed_this_morning_are_gone_at_11_01():
    t = _full()
    closed = {"800749-2026", "800869-2026", "800964-2026"}  # 09:00, 09:00, 11:00 CET on 25 Nov
    before = set(m.open_lots(t, datetime(2026, 11, 25, 8, 59, tzinfo=CET), "", "", 30)
                 .column("tender_lots~notice").to_pylist())
    after = set(m.open_lots(t, datetime(2026, 11, 25, 11, 1, tzinfo=CET), "", "", 30)
                .column("tender_lots~notice").to_pylist())
    assert closed <= before and not closed & after


def test_participation_requests_are_found_and_labelled():
    t = _full()
    hits = m.open_lots(t, datetime(2026, 11, 25, 11, 1, tzinfo=CET), "", "DE", 30, m.REQUEST)
    rows = hits.to_pylist()
    assert len(rows) == 16 and {r["tender_lots~action"] for r in rows} == {"request to participate"}
    ex = [r for r in rows if r["tender_lots~notice"] == "800308-2026" and r["tender_lots~lot"] == "LOT-0001"]
    assert ex and ex[0]["tender_lots~until"].startswith("2026-12-09")
    assert ex[0]["tender_lots~cite_request_deadline"]


def test_country_and_region_contradiction_is_flagged_and_not_counted_as_germany():
    t = _full()
    rows = [r for r in t.to_pylist() if r["tender_lots~notice"] == "800917-2026"]
    assert len(rows) == 3 and all("DE300 vs ESP" in r["tender_lots~location_conflict"] for r in rows)
    de = m.open_lots(t, NOON, "", "DE", 30).column("tender_lots~notice").to_pylist()
    assert "800917-2026" not in de


def test_query_time_without_time_zone_is_refused():
    with pytest.raises(ValueError, match="time zone"):
        m.open_lots(_full(), datetime(2026, 11, 25, 12, 0), "", "", 30)


def test_a_later_correction_of_one_side_of_a_fork_stays_in_conflict(tmp_path):
    # B and B2 both replace A (a fork), then D replaces B: D must not answer while the fork is open
    d = _pair(tmp_path)
    raw = (ROOT / "data" / "xml" / f"{B}.xml").read_text(encoding="utf-8")
    (d / "xml" / "B2-synthetic.xml").write_text(
        raw.replace("9bcdb08c-aae7-41a4-96e7-f73bf14bc1d1", "00000000-0000-4000-8000-0000000000b2"), encoding="utf-8")
    (d / "xml" / "D-synthetic.xml").write_text(
        raw.replace("9bcdb08c-aae7-41a4-96e7-f73bf14bc1d1", "00000000-0000-4000-8000-00000000000d")
           .replace("150ae3e7-899c-4c62-980e-9b01093470e9-01", "9bcdb08c-aae7-41a4-96e7-f73bf14bc1d1-01"),
        encoding="utf-8")
    t = _run(d)
    status = {r["tender_lots~notice"]: r["tender_lots~version_status"] for r in t.to_pylist()}
    assert status["D-synthetic"] == "conflict"
    assert "D-synthetic" not in _notices(t)


def test_citation_checker_fails_on_an_empty_or_incomplete_snapshot(tmp_path):
    import subprocess
    (tmp_path / "xml").mkdir()
    shutil.copy(ROOT / "data" / "fields.json", tmp_path / "fields.json")
    (tmp_path / "ids.txt").write_text("800869-2026\n")
    run = subprocess.run([sys.executable, "-I", str(ROOT / "src" / "check_citations.py"), str(tmp_path)],
                         capture_output=True, text=True, check=False)
    assert run.returncode != 0 and "refused" in run.stderr
    (tmp_path / "ids.txt").unlink()
    run = subprocess.run([sys.executable, "-I", str(ROOT / "src" / "check_citations.py"), str(tmp_path)],
                         capture_output=True, text=True, check=False)
    assert run.returncode != 0 and "empty snapshot" in run.stderr


def test_totals_keep_cents():
    t = _full()
    out = m.stated_total(m.open_lots(t, datetime(2026, 11, 25, 11, 1, tzinfo=CET), "", "DE", 30, m.TENDER))
    assert out.startswith("1,185,833.35 EUR over 11 of 86 lots")
