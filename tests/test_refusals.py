import shutil
import sys
from pathlib import Path

import pyarrow as pa
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import eforms_mloda as m

SAMPLE = ["800869-2026.xml", "800306-2026.xml"]


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
    with pytest.raises(Exception, match="not eForms notices"):
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
