# Plan tab: target entry + reference star finder

> **Changed by Matt (2026-11-16, after the first build):** the target and reference are NOT linked into Files or Response. The Plan tab produces an observing plan (target name, V, RA, Dec beside reference name, V, RA, Dec) as text or JSON to copy or save. Step 4 and the "shows up in Files and Response" acceptance below are superseded.

Scope is deliberately small. Only these features are brought over from the older SpectroTargetTool (the author's earlier tool). Do NOT bring over catalog sources (Gaia, TNS, ARAS, CBAT, VSX campaigns), ranking, SNR, visibility curves, N.I.N.A. export, the CustomTkinter GUI or caching.

## What the user does
1. Types a target name (or pastes a designation).
2. Clicks **Check with SIMBAD**. On success, RA/Dec (J2000), V magnitude and object type fill in. On failure or offline, the user types **RA/Dec manually** (needed for novae/transients that SIMBAD does not know yet). Manual RA/Dec accepts `hh mm ss.s` / `dd mm ss` and decimal degrees.
3. The tool lists the **N closest reference-star candidates** (default 10, user can change N) from a bundled catalog: name, distance on sky (deg), V, B-V, spectral type.
4. The user picks one row as the reference star (optional). The choice and the target go into `session.json` and prefill the Files tab (target name, RA/Dec, reference name) and the Response tab (reference spectral type, which maps to the Pickles template).

## What to port (and from where)
All paths relative to the SpectroTargetTool project.

| Source | Use |
|---|---|
| `spectro_planner/astronomy/reference_stars.py` | Port the logic as is: load CSV, great-circle distance with numpy, sort, take n. Dataclasses `ReferenceStar`, `ReferenceStarMatch`. Remove `lru_cache` import path assumptions if they conflict; keep the function names |
| `spectro_planner/data/reference_stars.csv` | Copy to `starex_reduce/data/reference_stars.csv`. 1041 stars, V < 6, mostly A and B main-sequence stars, columns `name,ra_deg,dec_deg,v_mag,b_v,sp_type`. Originates from F. Teyssier's Reference Star Finder spreadsheet (v3, 2024), itself built from SIMBAD; keep that credit in the README |
| `tests/test_reference_stars.py` | Port the 4 tests unchanged (adjust imports) |

Do not copy anything else from that repo. Do not import from it at runtime.

## What the old logic does and does not do (so nobody assumes more)
- It ranks purely by **angular distance** to the target. No altitude, airmass, hour angle, reddening or time-of-night logic.
- The original spreadsheet (`ReferenceStarFinder_V3_en.xlsm`) also has: an observing site/date/UT input, a sort-by choice (distance or hour-angle difference "D h"), an E(B-V) reddening column with limits 0.05/0.1, and a Miles-library flag. The Python port dropped these. They are NOT part of this task; see "Optional add-ons".

## Reuse what the new app already has
- SIMBAD/Sesame lookup: use `starex_reduce/core/` code created at M1 (ported from `specinti_prep/simbad.py`, with offline fallback). Do not write a second one. If M1 is not done yet, create it once in `core/simbad.py` and have Files use it too.
- Manual RA/Dec parsing: reuse the existing coordinate parser if there is one.
- Network rules in CLAUDE.md still hold: SIMBAD/Sesame only on a user click, never in loops. The reference catalog lookup is local and instant.

## Implementation plan
1. `core/reference_stars.py` (no Qt): port as above, plus `find_reference_stars(ra_deg, dec_deg, n)` and a tiny `parse_coords()` if missing. Vectorised numpy, load the catalog once.
2. `core/session.py`: add `target` (name, ra_deg, dec_deg, source: "simbad"|"manual", vmag, otype) and `reference` (name, ra, dec, v, bv, sp_type, distance_deg, chosen: bool) to the session model; persist in `session.json`; changing the target clears the reference choice.
3. `ui/plan_tab.py`: the tab replaces the placeholder "Plan an observing night ... Not built yet." Layout: card "Target" (name field, Check with SIMBAD button, RA and Dec fields, status chip showing source SIMBAD/Manual) above a card "Reference star candidates" (N spinner, results table, "Use as reference" button). Match the existing theme and widget style; follow `docs/UI.md` conventions. SIMBAD lookup runs in the job runner (not the UI thread); table filling is instant and can run inline.
4. Wire-up: Files tab reads target name/RA/Dec from the session; Response tab reads the reference spectral type (strip luminosity class, map to the Pickles file with the existing mapping).
5. Tests: port the 4 reference-star tests; add tests for parse_coords, for "manual RA/Dec works with SIMBAD offline", for session persistence round-trip, and a pytest-qt test that typing RA/Dec fills the table with 10 rows sorted by distance.
6. Docs: add a short "Plan tab" section to `docs/UI.md` and a line to `docs/SPEC.md`; credit the catalog in README.

## Acceptance
- Typing `gam Cas` and clicking Check with SIMBAD fills RA/Dec within 3 s; with the network blocked the button shows a clear message and manual entry still works.
- Entering RA/Dec of a nova (e.g. 18 53 02 -28 20 00) lists 10 candidates sorted by distance, nearest first; row for an exact catalog position returns that star first (distance < 0.001 deg).
- Choosing a reference persists across app restart and shows up in Files and Response.
- No new dependency beyond what is already in pyproject.toml.

## Optional add-ons (NOT in scope unless the user asks)
a. Altitude/airmass of each candidate at a chosen date/UT from the site in settings (the spreadsheet's "Trajectoire" idea), and sort by hour-angle difference.
b. E(B-V) reddening column and a limit filter (spreadsheet columns U and limits 0.05/0.1).
c. "Template available" flag showing whether a Pickles template exists for the star's spectral type.
