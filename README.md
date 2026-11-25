# eForms spike: tender lots with field-level citations, on mloda 0.22.0

This is a proof of concept for the November mloda proposal. It reads real EU tender notices from TED,
in eForms XML, into an mloda feature, `tender_lots`. Each value records the notice, lot, eForms
business term and XPath it came from.

## What it shows (run on 25 Nov 2026)

| Check | Result |
|---|---|
| Source | 99 real German contract notices (`cn-standard`), published 18–19 Nov 2026 (dispatched 16–17 Nov), fetched from the public TED API. The full week, `notice-type = cn-standard AND place-of-performance IN (DEU) AND publication-date >= 20261118 AND publication-date <= 20261125`, returns 1,882 notices |
| Through mloda | `mloda.run_all(Feature("tender_lots"))` gives 164 lots in 17 columns. The reader is a `SourceFeatureGroup`, the same pattern as German Ledger. |
| Schema-driven | XPaths are read from the official eForms SDK `fields.json` (1.13.0). The notices use SDK 1.13 and 1.14, and both parse. |
| Citations | 168 of 168 cited values re-resolve to the same value when the XPath is evaluated with lxml, an independent engine |
| Refusals (tests) | an HTTP 429 page saved as `.xml` makes the run refuse. Mixed currencies are refused in a total, and when no lot states a value the answer is "no total", not 0. |
| Honesty | only 25 of 164 lots state an estimated value (BT-27). Totals say "15 of 92 lots" and never count a missing value as 0. |
| Change notices | 34 of 99 notices are change notices (BT-758). `src/resolve_changes.py` resolves **34 of 34** to the exact notice they replace through the TED search API (BT-758 = the changed notice's identifier plus its version). The originals were published from June 2025 to 17 Nov 2026, all outside the sample. Result in `data/changes.json`. |

## Run

```sh
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python "mloda==0.22.0" pyarrow lxml pytest
python3 -I src/fetch_data.py data                                # SDK fields.json + the 99 notices in data/ids.txt
.venv/bin/python -I src/eforms_mloda.py "$PWD/data" 2026-11-25   # two questions, cited answers
.venv/bin/python -I src/check_citations.py "$PWD/data"           # re-resolve every citation with lxml
.venv/bin/python -m pytest -q tests                              # refusal tests through mloda
python3 -I src/resolve_changes.py data                            # link each change notice to the notice it replaces (TED API)
```

## Files

- `src/eforms_lots.py`: the core (stdlib). It maps SDK field to XPath, builds lot rows with citations, and applies two rules: which value applies to a lot, and when a notice is superseded.
- `src/eforms_mloda.py`: the `EformsLotsReader` `SourceFeatureGroup`, plus the `open_lots` filter and a total that refuses mixed currencies.
- `src/check_citations.py`: checks every citation independently.
- `tests/test_refusals.py`: four end-to-end tests through `mloda.run_all`.
- `src/fetch_data.py`: rebuilds `data/` from public sources. `data/ids.txt` lists the 99 TED publication numbers. The notice XML isn't committed because it holds procurement officers' contact details; TED publishes it, and this repo doesn't need copies. `fields.json` is eForms SDK 1.13.0 from github.com/OP-TED/eForms-SDK (EUPL-1.2).

## Not done yet (the 1–15 Dec plan)

- Split out the general `xml` base reader. The mapping is already data (SDK fields), but it lives in one module.
- Fetch the resolved originals and mark their lots superseded inside `tender_lots` (the link itself already resolves, see above).
- Use `defusedxml`, run the registry's `SourceFeatureGroupTestMixin` conformance suite, and package for mloda-registry.
- Turn `open_lots` into an mloda feature group with CPV parent codes and NUTS levels, instead of a pyarrow filter.

## License

Apache-2.0 for the code in this repo. TED notices are published by the EU Publications Office and the eForms SDK is EUPL-1.2. Neither is redistributed here.
