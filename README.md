# eForms spike: tender lots with field-level citations, on mloda 0.22.0

This is a proof of concept for the November mloda proposal. It reads real EU tender notices from TED,
in eForms XML, into an mloda feature, `tender_lots`. Each value records the notice, lot, eForms
business term and XPath it came from.

## What it shows (run on 25 Nov 2026)

| Check | Result |
|---|---|
| Source | 99 real German contract notices (`cn-standard`), published 18 Nov 2026 (dispatched 16–17 Nov), fetched from the public TED API. The full week, `notice-type = cn-standard AND place-of-performance IN (DEU) AND publication-date >= 20261118 AND publication-date <= 20261125`, returns 1,882 notices |
| Through mloda | `mloda.run_all(Feature("tender_lots"))` reads the 99-notice sample plus the 34 replaced originals: **225 lot rows in 26 columns**, of which 164 are `current_in_snapshot` (the 99 sample notices) and 61 `superseded`. The reader is a `SourceFeatureGroup`, the same pattern as German Ledger. |
| Schema-driven | XPaths are read from the official eForms SDK `fields.json` (1.13.0). The notices use SDK 1.13 and 1.14, and both parse. |
| Citations | All **480 of 480** citations in that run re-resolve to the same text when the XPath is evaluated with lxml on the file each row was read from: tender deadline date and time (BT-131), request-to-participate date and time (BT-1311) and estimated value (BT-27). Title, CPV, region and country carry no citation yet. The checker applies the same manifest check as the reader and fails on an empty snapshot or any mismatch. |
| Tests | 22 tests through `mloda.run_all` and the checker: exact closing times (three lots that closed at 09:00/11:00 on 25 Nov are in at 08:59 and gone at 11:01); 16 request-to-participate lots found and labelled; a country/region contradiction flagged and kept out of the Germany count; a query time without a time zone refused; a fork that is later corrected again stays `conflict`; the checker fails on an empty or incomplete snapshot; totals keep cents; plus refusals (HTTP 429 page, notice without lots or identity, missing or unlisted file, identity conflict, mixed or missing currency) and version handling (duplicate counted once, superseded original excluded, A → B → C resolves to C). |
| What a supplier can do | Query instant **2026-11-25T11:01:00+01:00**, 30 days, a place of performance in Germany (an address whose region and country agree): **102 lots, 86 "submit tender" and 16 "request to participate"**, each with its deadline in local time and a citation. Stated values: **1,185,833.35 EUR on 11 of 86** tender lots and **48,324,280.64 EUR on 7 of 16** request lots; missing values are never counted as 0. Construction in Bavaria: 6 lots, none states a value. `800917-2026` (addresses say Spain, regions say France, Germany and Ireland) is flagged and not counted as German. |
| Change notices | 34 of 99 notices are change notices (BT-758). `src/resolve_changes.py` resolves **34 of 34** to the exact notice they replace through the TED search API (BT-758 = the changed notice's identifier plus its version). Version status comes from that link only: `superseded`, `current_in_snapshot` (nothing loaded replaces it), `unresolved` (a change notice whose original isn't loaded) or `conflict` (carried down later corrections). **10 lots in 7 notices look closed in the original but have a deadline after 25 Nov in the correction** (e.g. `686722-2026` 20 Oct → `801029-2026` 27 Nov). `src/compare_changes.py` compares date, time, request deadline, amount and currency: 20 of 34 corrections move a deadline date, 7 a deadline time, 1 a request deadline, 1 a value, 1 a title. |

## Run

```sh
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python "mloda==0.22.0" pyarrow lxml pytest
python3 -I src/fetch_data.py data                                # SDK fields.json + the 99 notices in data/ids.txt
.venv/bin/python -I src/eforms_mloda.py "$PWD/data" 2026-11-25T11:01:00+01:00   # what can a supplier still do, cited
.venv/bin/python -I src/check_citations.py "$PWD/data"           # re-resolve every citation (sample + originals) with lxml
.venv/bin/python -m pytest -q tests                              # refusal tests through mloda
python3 -I src/resolve_changes.py data                            # link each change notice to the notice it replaces (TED API)
python3 -I src/fetch_data.py data                                # again: now also fetches the 34 replaced originals
.venv/bin/python -I src/compare_changes.py data                  # what each correction changes, cited on both sides
```

## Files

- `src/eforms_lots.py`: the core (stdlib). It maps SDK field to XPath, builds lot rows with citations, and applies two rules: which value applies to a lot, and when a notice is superseded.
- `src/eforms_mloda.py`: the `EformsLotsReader` `SourceFeatureGroup`, plus `open_lots` (exact query time, stage, deadline in local time, consistent location) and a decimal total that refuses mixed or missing currencies.
- `src/check_citations.py`: checks every citation of the run independently, in the sample and the originals; each XPath must select exactly one element, and any mismatch exits non-zero.
- `tests/test_refusals.py`: 15 tests through `mloda.run_all` (refusals, identity, chains, completeness).
- `src/fetch_data.py`: rebuilds `data/` from public sources. `data/ids.txt` lists the 99 TED publication numbers. The notice XML isn't committed because it holds procurement officers' contact details; TED publishes it, and this repo doesn't need copies. `fields.json` is eForms SDK 1.13.0 from github.com/OP-TED/eForms-SDK (EUPL-1.2).

## Known limits

- Deadlines without a time (date only) are kept and labelled "date only, no time given"; their exact cut-off is unknown.
- Region codes and countries are compared per address; a lot whose only German address contradicts its country isn't counted as German. Title, CPV, region and country have no citation yet.
- `resolve_changes.py` reads one page of 100 results per procedure (the largest here has 7) and doesn't follow originals' own predecessors yet.
- Tests use the real TED snapshot (fetch first); small synthetic fixtures and CI are planned.

## Not done yet (the 1–15 Dec plan)

- Split out the general `xml` base reader. The mapping is already data (SDK fields), but it lives in one module.
- Resolve change notices on every fetch, so originals and corrections from different fetch windows meet in one `tender_lots` table.
- Use `defusedxml`, run the registry's `SourceFeatureGroupTestMixin` conformance suite, and package for mloda-registry.
- Turn `open_lots` into an mloda feature group with CPV parent codes and NUTS levels, instead of a pyarrow filter.

## License

Apache-2.0 for the code in this repo. TED notices are published by the EU Publications Office and the eForms SDK is EUPL-1.2. Neither is redistributed here.
