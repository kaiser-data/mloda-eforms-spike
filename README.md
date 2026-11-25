# eForms spike: tender lots with field-level citations, on mloda 0.22.0

This is a proof of concept for the November mloda proposal. It reads real EU tender notices from TED,
in eForms XML, into an mloda feature, `tender_lots`. Each value records the notice, lot, eForms
business term and XPath it came from.

## What it shows (run on 25 Nov 2026)

| Check | Result |
|---|---|
| Source | 99 real German contract notices (`cn-standard`), published 18 Nov 2026 (dispatched 16–17 Nov), fetched from the public TED API. The full week, `notice-type = cn-standard AND place-of-performance IN (DEU) AND publication-date >= 20261118 AND publication-date <= 20261125`, returns 1,882 notices |
| Through mloda | `mloda.run_all(Feature("tender_lots"))` reads the 99-notice sample plus the 34 replaced originals: **225 lot rows in 18 columns**, of which 164 are `current_in_snapshot` (the 99 sample notices) and 61 `superseded`. The reader is a `SourceFeatureGroup`, the same pattern as German Ledger. |
| Schema-driven | XPaths are read from the official eForms SDK `fields.json` (1.13.0). The notices use SDK 1.13 and 1.14, and both parse. |
| Citations | All **229 of 229** citations in that run (168 in the sample, 61 in the originals) re-resolve to the same value when the XPath is evaluated with lxml, an independent engine, on the file each row was read from. |
| Tests | 11 tests, all through `mloda.run_all`: an HTTP 429 page saved as `.xml` is refused; a download missing from `ids.txt` or `changes.json` is refused; the same notice in both folders is counted once, and the same identity with different content is refused as a conflict; a superseded original whose deadline is inside the window is still excluded; a chain A → B → C resolves to C; mixed currencies are refused in a total; no stated value gives "no total", not 0. |
| Honesty | only 25 of 164 lots state an estimated value (BT-27). Totals say "15 of 92 lots" and never count a missing value as 0. |
| Change notices | 34 of 99 notices are change notices (BT-758). `src/resolve_changes.py` resolves **34 of 34** to the exact notice they replace through the TED search API (BT-758 = the changed notice's identifier plus its version). Version status comes from that link only: `superseded`, `current_in_snapshot` (nothing loaded replaces it), `unresolved` (a change notice whose original isn't loaded) or `conflict`. **10 lots in 7 notices look closed in the original but have a deadline after 25 Nov in the correction** (e.g. 20 Oct → 27 Nov; 13 lots in 10 notices if deadlines on 25 Nov itself count, but three of those closed that morning). `src/compare_changes.py`: 20 of 34 corrections move a deadline, one changes a value, one a title. Open-lot answers are unchanged, because superseded rows are excluded. |

## Run

```sh
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python "mloda==0.22.0" pyarrow lxml pytest
python3 -I src/fetch_data.py data                                # SDK fields.json + the 99 notices in data/ids.txt
.venv/bin/python -I src/eforms_mloda.py "$PWD/data" 2026-11-25   # two questions, cited answers
.venv/bin/python -I src/check_citations.py "$PWD/data"           # re-resolve every citation (sample + originals) with lxml
.venv/bin/python -m pytest -q tests                              # refusal tests through mloda
python3 -I src/resolve_changes.py data                            # link each change notice to the notice it replaces (TED API)
python3 -I src/fetch_data.py data                                # again: now also fetches the 34 replaced originals
.venv/bin/python -I src/compare_changes.py data                  # what each correction changes, cited on both sides
```

## Files

- `src/eforms_lots.py`: the core (stdlib). It maps SDK field to XPath, builds lot rows with citations, and applies two rules: which value applies to a lot, and when a notice is superseded.
- `src/eforms_mloda.py`: the `EformsLotsReader` `SourceFeatureGroup`, plus the `open_lots` filter and a total that refuses mixed currencies.
- `src/check_citations.py`: checks every citation of the run independently, in the sample and the originals.
- `tests/test_refusals.py`: 11 tests through `mloda.run_all` (refusals, identity, chains, completeness).
- `src/fetch_data.py`: rebuilds `data/` from public sources. `data/ids.txt` lists the 99 TED publication numbers. The notice XML isn't committed because it holds procurement officers' contact details; TED publishes it, and this repo doesn't need copies. `fields.json` is eForms SDK 1.13.0 from github.com/OP-TED/eForms-SDK (EUPL-1.2).

## Not done yet (the 1–15 Dec plan)

- Split out the general `xml` base reader. The mapping is already data (SDK fields), but it lives in one module.
- Resolve change notices on every fetch, so originals and corrections from different fetch windows meet in one `tender_lots` table.
- Use `defusedxml`, run the registry's `SourceFeatureGroupTestMixin` conformance suite, and package for mloda-registry.
- Turn `open_lots` into an mloda feature group with CPV parent codes and NUTS levels, instead of a pyarrow filter.

## License

Apache-2.0 for the code in this repo. TED notices are published by the EU Publications Office and the eForms SDK is EUPL-1.2. Neither is redistributed here.
