# Reference stars: where the list comes from and how to update it

The Plan tab's reference-star list (`starextractor/data/reference_stars.csv`) is generated outside the app by the
**starex_refcat** project (a separate repository; locally `B:\AI_Projects\starex_refcat`). It is our own selection
of bright B and A main-sequence stars from SIMBAD, with the AAVSO VSX and the Washington Double Star Catalog
(through VizieR) and the Pecaut and Mamajek (2013) intrinsic colours, by rules documented there
(`docs/REFERENCE_CATALOG.md`, the config in `config/`, one build's numbers in `docs/reference_catalog_report.md`).
For observers, the Help guide explains it: **Where the reference stars come from**.

Bundled now: starex_refcat **v0.1.0**, preset **balanced**, V ≤ 6.5: 1595 stars (106 good, 351 ok, 1138 poor).
`starextractor/data/reference_stars.meta.json` holds the provenance (generator version, preset, V limit, query and
config hashes, SIMBAD endpoint and retrieval date, counts).

## Columns (schema 2)

The first six are the ones the loader has always required: `name, ra_deg, dec_deg, v_mag, b_v, sp_type`. Then
`alt_ids, ebv, type_source, n_type_measurements, n_type_consistent, vsini, distance_pc, v_class, grade, reasons,
reason_text, verified, notes` (empty = unknown). `core/reference_stars.py` reads them with `row.get(...)`, so an old
six-column file still loads (no grades: every row is shown, with the old spectral-type flag).

## Updating the list

1. In starex_refcat (never in this repository): change the rules or the preset there, then regenerate with
   `tools/build_reference_catalog.py build` (add `--refresh` to query SIMBAD and VizieR again). Never edit the CSV by
   hand.
2. Copy its `data/reference_stars.csv` and `data/reference_stars.meta.json` over the two files in
   `starextractor/data/`.
3. Update the numbers in `starextractor/resources/help.md` (Where the reference stars come from: stars, grades,
   generator version, preset, retrieval date, coverage) from the meta file and the generator's report;
   `tests/test_help.py` checks them against the meta file.
4. Run `ruff check .` and `pytest`: `tests/test_reference_catalogue.py` checks the columns, values, grades, V
   classes, that every spectral type reaches a Response template, the load and lookup times and that nothing goes
   online. Add a CHANGELOG line.

The PyInstaller spec bundles the whole `starextractor/data` folder, so both files go into the build; `--selftest`
checks that the meta file matches the list.
