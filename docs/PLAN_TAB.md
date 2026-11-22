# Plan tab: target entry + reference star finder

> **Changed by Matt (2026-11-16, after the first build):** the target and reference are NOT linked into Select (then called Files) or Response. The Plan tab produces an observing plan (target name, V, RA, Dec beside reference name, V, RA, Dec) as text or JSON to copy or save. Step 4 and the "shows up in Files (now Select) and Response" acceptance below are superseded.

Scope is deliberately small. Only these features are brought over from the older SpectroTargetTool (the author's earlier tool). Do NOT bring over catalog sources (Gaia, TNS, ARAS, CBAT, VSX campaigns), ranking, SNR, N.I.N.A. export, the CustomTkinter GUI or caching. (Visibility plots were added on 2026-11-20: see *Observing conditions* below.)

## What the user does
1. Types a target name (or pastes a designation).
2. Clicks **Check with SIMBAD**. On success, RA/Dec (J2000), V magnitude and object type fill in. On failure or offline, the user types **RA/Dec manually** (needed for novae/transients that SIMBAD does not know yet). Manual RA/Dec accepts `hh mm ss.s` / `dd mm ss` and decimal degrees.
3. The tool lists the **N closest reference-star candidates** (default 10, user can change N) from a bundled catalog: name, distance on sky (deg), V, B-V, spectral type.
4. The user picks one row as the reference star (optional). The choice and the target go into `session.json` and prefill the Files tab (now Select) (target name, RA/Dec, reference name) and the Response tab (reference spectral type, which maps to the Pickles template).

## What to port (and from where)
All paths relative to the SpectroTargetTool project.

| Source | Use |
|---|---|
| `spectro_planner/astronomy/reference_stars.py` | Port the logic as is: load CSV, great-circle distance with numpy, sort, take n. Dataclasses `ReferenceStar`, `ReferenceStarMatch`. Remove `lru_cache` import path assumptions if they conflict; keep the function names |
| `spectro_planner/data/reference_stars.csv` | Copy to `starextractor/data/reference_stars.csv`. 1041 stars, V < 6, mostly A and B main-sequence stars, columns `name,ra_deg,dec_deg,v_mag,b_v,sp_type`. Credit (as in About and the README): "A subset, with amendments, of the Reference Star Finder database by François Teyssier and Paolo Berardi (data from SIMBAD)" (v3 spreadsheet, 2024); keep it in the README |
| `tests/test_reference_stars.py` | Port the 4 tests unchanged (adjust imports) |

Do not copy anything else from that repo. Do not import from it at runtime.

## What the old logic does and does not do (so nobody assumes more)
- It ranks purely by **angular distance** to the target. No altitude, airmass, hour angle, reddening or time-of-night logic.
- The original spreadsheet (`ReferenceStarFinder_V3_en.xlsm`) also has: an observing site/date/UT input, a sort-by choice (distance or hour-angle difference "D h"), an E(B-V) reddening column with limits 0.05/0.1, and a Miles-library flag. The Python port dropped these. They are NOT part of this task; see "Optional add-ons".

## Reuse what the new app already has
- SIMBAD/Sesame lookup: use `starextractor/core/` code created at M1 (ported from `specinti_prep/simbad.py`, with offline fallback). Do not write a second one. If M1 is not done yet, create it once in `core/simbad.py` and have Files use it too.
- Manual RA/Dec parsing: reuse the existing coordinate parser if there is one.
- Network rules in CLAUDE.md still hold: SIMBAD/Sesame only on a user click, never in loops. The reference catalog lookup is local and instant.

## Implementation plan
1. `core/reference_stars.py` (no Qt): port as above, plus `find_reference_stars(ra_deg, dec_deg, n)` and a tiny `parse_coords()` if missing. Vectorised numpy, load the catalog once.
2. `core/session.py`: add `target` (name, ra_deg, dec_deg, source: "simbad"|"manual", vmag, otype) and `reference` (name, ra, dec, v, bv, sp_type, distance_deg, chosen: bool) to the session model; persist in `session.json`; changing the target clears the reference choice.
3. `ui/plan_tab.py`: the tab replaces the placeholder "Plan an observing night ... Not built yet." Layout: card "Target" (name field, Check with SIMBAD button, RA and Dec fields, status chip showing source SIMBAD/Manual) above a card "Reference star candidates" (N spinner, results table, "Use as reference" button). Match the existing theme and widget style; follow `docs/UI.md` conventions. SIMBAD lookup runs in the job runner (not the UI thread); table filling is instant and can run inline.
4. Wire-up: Files tab (now Select) reads target name/RA/Dec from the session; Response tab reads the reference spectral type (strip luminosity class, map to the Pickles file with the existing mapping).
5. Tests: port the 4 reference-star tests; add tests for parse_coords, for "manual RA/Dec works with SIMBAD offline", for session persistence round-trip, and a pytest-qt test that typing RA/Dec fills the table with 10 rows sorted by distance.
6. Docs: add a short "Plan tab" section to `docs/UI.md` and a line to `docs/SPEC.md`; credit the catalog in README.

## Acceptance
- Typing `gam Cas` and clicking Check with SIMBAD fills RA/Dec within 3 s; with the network blocked the button shows a clear message and manual entry still works.
- Entering RA/Dec of a nova (e.g. 18 53 02 -28 20 00) lists 10 candidates sorted by distance, nearest first; row for an exact catalog position returns that star first (distance < 0.001 deg).
- Choosing a reference persists across app restart and shows up in Files (now Select) and Response.
- No new dependency beyond what is already in pyproject.toml.

## Optional add-ons (NOT in scope unless the user asks)
a. Altitude/airmass of each candidate at a chosen date/UT from the site in settings (the spreadsheet's "Trajectoire" idea), and sort by hour-angle difference.
b. E(B-V) reddening column and a limit filter (spreadsheet columns U and limits 0.05/0.1).
c. "Template available" flag showing whether a Pickles template exists for the star's spectral type.

## Layout (2026-11-21)

Layout only: every card keeps its behaviour, state and settings. `ui/screens/plan.py` builds a header and three rows
in the page's vertical scroll area (no horizontal scrolling at the minimum window size, 1100×700). Alignment fixed the
same day: the header spans the page, the two columns start and end level, the side margins are equal.

```
|<-32->|                                                                                 |<-32->|
       Plan
       Enter the target, then pick a reference star close to it on the sky. The observing ...
       +-------------------------------------------+ +-------------------------------------------+
       | Target                                    | | Observing conditions                  (i) |
       | [name ....................] (Check SIMBAD)| | Latitude [  ] Longitude [  ] (Use my loc.)|
       | ✓ SIMBAD: ... / – Position typed by hand  | | from settings                             |
       | RA [  ] ⧉  Dec [  ] ⧉  [SIMBAD|Manual]    | | Date [   ] From [ ] To [ ] (Astro night)  |
       +-------------------------------------------+ | Min altitude [25°]                        |
       +-------------------------------------------+ | Times use your computer's time zone (UTC) |
       | Reference star candidates  Show [10] (Use)| | Above 25° from 20:00 to 03:09 · ...       |
       | Name  Dist  V  B−V  Sp.  Flux ref         | +-------------------------------------------+
       | ✓ HD 4222 ...                             | +-------------------+ +---------------------+
       | ...                                       | | Sky               | | Altitude            |
       | ✓ Reference: HD 4222 · A2Vs · V 5.41 ...  | |    ( circle,      | |  chart (fills the   |
       |                                           | |      centred )    | |  card's height)     |
       |   (stretches when the right is taller)    | | legend            | | legend              |
       +-------------------------------------------+ +-------------------+ +---------------------+
       <------------- equal width --------------> 10 <-------- 45 % ---------> 10 <---- 55 % ---->
       +-----------------------------------------------------------------------------------------+
       | Planned pairs                                                                       (i) |
       | #  Target     Reference   Visible        Moon sep.  Status                              |
       | 1  bet Cas    HD 224572   20:00–03:09    47°        (● In Acquisition)                  |
       | 2  V1405 Cas  HD 224572   20:00–02:30    52°        Ready                               |
       | 3  P Cyg      —           20:00–22:33    73°        [! pick reference]                  |
       | Note [.........................................................................]        |
       | (Add pair)(Update pair)(Remove)(Move up)(Move down)(Sort by transit time) [Load for acq]|
       | ✓ Added bet Cas + HD 224572.                                                            |
       +-----------------------------------------------------------------------------------------+
       | Observing plan (i)      [Text|JSON] (Copy)(Save…)(Open plan…)(Target RA+Dec)(Ref RA+Dec) |
       | | monospace plan, 9 lines high, its own vertical scrollbar                            | |
       +-----------------------------------------------------------------------------------------+ ‖ <- page
                                                                                                    scrollbar
```

- **Header:** the title "Plan" and the intro line span the page above both columns, so the Target card and the
  Observing conditions card start at the same height.
- **Row 1:** two columns of equal width (stretch 1:1, horizontal size policy Ignored so their contents never make one
  wider; at least 420 px each), a 10 px gap and 10 px between cards, as on the other tabs. Left: **Target**,
  **Reference star candidates**. Right: **Observing conditions**, then two cards side by side, **Sky** (the polar
  plot, about 45 %) and **Altitude** (the chart and its legend: Target, Moon, Min altitude, Now; about 55 %).
  The columns end level: the last card of each column takes the spare height (the Reference card keeps its rows at
  the top; the Sky and Altitude cards grow together, the circle stays square and centred, the chart fills its card
  and is never shorter than the circle). No card has a fixed height.
- **Margins and the page scrollbar:** the tab area has 32 px on both sides (`main_window`). The page's own scrollbar
  would take 10 px inside that and make the right margin look wider, so the Plan page hides it and shows a
  `MarginScrollBar` (`ui/widgets/margin_scroll.py`) centred in the window's right margin band instead: synced both
  ways (range, page step, value), shown only while there is something to scroll, never over a card. The wheel and
  the keyboard still scroll the page. Window edge to the outermost card: 32 px left and right, scrolling or not.
- **Wrapping rows** (`ui/widgets/flow.py`): the Target card's RA/Dec line, the conditions card's two field lines and
  the chart legend wrap when the column is narrow; a label stays with its field. "Check with SIMBAD" keeps its full
  text and the name field shrinks. The time zone line appears once, under the fields.
- **Tables** (`ui/widgets/table_cols.py`): columns share the table width in fixed proportions, never narrower than
  their header or the widest thing they show (Reference candidates: Name, V, B−V and the "! poor flux ref" chip have
  their own minimum; Planned pairs: # narrowest, Status widest). The candidates table has 6 px cell padding
  (`QTableWidget#planRefs`) so six columns fit half the page at the minimum size.
- **Row 2:** Planned pairs, full width. Status: a filled green-family pill **● In Acquisition**, plain muted
  **Ready**, an outlined amber tag **! pick reference** (the Reference cell shows "—"); text and shape tell them apart
  in every palette. The buttons in order, a stretch, then the one primary button **Load for acquisition**, right
  aligned; the result line under them.
- **Row 3:** Observing plan, full width; the plan view has a fixed height of nine text lines and its own vertical
  scrollbar, so the page does not grow with the number of pairs.
- **What does not fit:** at 1100×700 each column is 513 px. The RA/Dec line puts the source chip on a second line;
  the conditions card puts the location tag, *Astronomical night* and *Min altitude* on further lines; the altitude
  chart labels every 3 to 6 hours instead of every 2. Long names and spectral types in the candidates table end in
  "…". At 1280×800 only the location tag and *Min altitude* wrap (line 2 needs about 690 px, the card has about 570).
- Tests: `tests/test_plan_layout.py` at 1280×800, the minimum size, 1920×1080 and 1920×2400 (no scrolling); sizes
  are measured by `tools/plan_layout_check.py` in a subprocess with the system fonts (the offscreen platform's
  fallback font is about twice as wide). Screenshots 35–35d (`tools/screenshot.py --all`).

## Observing conditions (2026-11-20)

At the top of the right column; the sky plot and the altitude chart are cards of their own under it (since
2026-11-21, see *Layout*; `ui/screens/plan_conditions.py`, sky maths in `core/sky.py`, the location rules in
`core/location.py`).

**Controls.** Latitude and Longitude (decimal degrees with sign, a N/S/E/W suffix, or degrees minutes seconds such
as `104 01 21 W`; north and east positive; validated, with an error line under the row), a muted source tag (*from
settings*, *from stack header*, *from Export*, *from internet*, *manual*), **↻ Use my location**, and **Reset to site
default** (only when the value differs from the site default). **Date** (the evening the window starts; default: the
evening of the window that is running or still to come, so after midnight it is yesterday's evening while that
window lasts), **From** / **To** in local time (default 20:00-06:00, may cross midnight), **Astronomical night** (sets
them to the Sun at -18° for the date and place) and **Min altitude** (default 25°). A note names the zone: "Times use
your computer's time zone (UTC-06:00)". It is the computer's zone, through `datetime.astimezone`.

**Visuals.** Left, a square polar sky plot (as wide as its card, 180 px at least; QPainter): N up, S down, E right, W left, zenith in the middle,
horizon outside, rings at 30° and 60° with labels, the minimum altitude as a red dashed ring; the target's path over
the window every 5 minutes while above the horizon (solid), an orange dot with its time where it rises above or sets
below the minimum altitude ("↑ rises 19:12", "↓ sets 22:46" in the legend) and a white dot "current pos" when now is
inside the plotted range. The reference star is not drawn. Right, the altitude chart (pyqtgraph): local time from the
window start - 2 h to the end + 2 h, ticks every 2 h (24-hour clock), 0-90° with grid lines at 30° and 60°; target
(solid, thick, lightly filled below), Moon (dashed, always drawn, also without a target), min altitude (dotted), now
(thin, only inside the range); background bands by Sun altitude (daylight warm, civil, nautical and astronomical
twilight fainter; in the night palettes brightness steps only). The legend shows each series' line style, so the
red-filter palettes can tell them apart. One summary line under both, e.g. "Above 25° from 20:00 to 20:58 · transit
15:34 at 82° · Moon 86° away, 85 % lit"; also "Always above the horizon", "Never rises at this latitude" and "Not above
25° in the window (highest 7°)". Without a location both plots show "Set your location to see the sky plots"; without
a target the polar plot shows its rings and "Enter the target position to see its path".

**Observing plan.** The text and JSON get **Date**, **Window** (from-to local, min altitude) and **Visible** (the
summary's first part). The coordinates of the location are never in it.

**Location (planning only).** Default order when the tab opens: (1) a location the user set here (typed, or Use my
location), kept in the settings as `plan.location` with its source and `user_set`; (2) otherwise the site that already
exists in the app: the saved site (`settings.site`, Export › Collect with *remember*), then the loaded pair's stack
headers (SITELAT/SITELONG, then OBSGEO-B/L, then LAT-OBS/LONG-OBS; 0-360° east longitudes are converted), then the
pair's own Export fields, shown with its tag and followed live until the user edits it; (3) otherwise empty. Editing
or Use my location stops the mirroring; Reset to site default clears `plan.location` and mirrors again.

**The one network call.** **Use my location** sends one HTTPS request to `https://ipwho.is/` (no key; a constant in
`core/location.py`) from a worker with a 5 s timeout and reads `latitude`/`longitude`. The result is tagged *from
internet* with the tooltip "Approximate location from your internet address. Check it or type your coordinates." On
failure (offline, timeout, an error or rate limit, an unreadable answer) one short line says why and nothing changes.
Nothing else goes online: not at start-up, not when the tab opens, not on edits, not on a timer. Privacy: the request
necessarily shows the service your internet address; nothing else is sent.

**Privacy of the location.** It is never written to a logbook, FITS header, export file, sets file, `session.json` or
the observing plan (tests: a full synthetic self-test run and the Plan's own files are searched for it).

**Speed.** One computation takes about 2 ms (all numpy, `core/sky.py`). It runs in a worker thread 150 ms after the
last edit; the plots only draw. The "now" marks move once a minute while the card is visible.

**Accuracy (`core/sky.py` against astropy, tests/test_sky.py).** Target alt/az within 0.008° (J2000 precessed to the
date; no nutation, aberration or refraction), Sun 0.007° (Meeus low precision), Moon 0.024° (Meeus ch. 47 main terms
with topocentric parallax), rise/set times within 2 minutes, at four sites (two southern) and four dates.
Reimplemented, not ported: the older SpectroTargetTool did this with astroplan/astropy
(`spectro_planner/astronomy/visibility.py`) and drew its altitude curve with matplotlib; it had no polar plot. There is
no astropy at run time here.

## The Moon on the polar plot (2026-11-21)

The polar plot draws the Moon's track over the whole plotted range (window ± 2 h) from the **same samples as the
altitude chart's Moon curve** (`SkyPlan.unix`, `moon_alt > 0`; one computation, two views): a dashed line in the Moon
series token (`SKY_TEXT`, amber in every palette), only above the horizon, under the target's solid path. A hollow
ring marks the Moon at "now" when now is inside the range, else at the middle of the window. The legend gains
"moon" (a dashed sample) and "moon now" (a ring) after the existing entries; with the Moon below the horizon over the
whole range nothing is drawn and the legend says "Moon below the horizon". Without a target the Moon is still drawn.
Series differ by style, not only colour (solid / dashed / filled dot / hollow ring), for the red-filter palettes.
Recalculation is unchanged (one `sky.compute`, about 2 ms).

## Planned pairs (2026-11-21)

Called **planned pairs** in the UI and the code so they are not confused with the **Pair** selector of the top row
(the pair loaded in the reduce flow, which this does not change). Code: `core/planned_pairs.py` (no Qt),
`ui/screens/plan_pairs.py`.

**Card.** "Planned pairs", the full-width row under the two columns and above the Observing plan. Table: #, Target, Reference,
Visible (e.g. `18:00–22:46` in the chosen window, `not above 25°`, `never rises`, `—` without a location), Moon sep.
(at the window middle), Status (a filled pill "● In Acquisition" for the pair loaded there; plain muted "Ready"; an
outlined amber "! pick reference" when the pair has no reference). Under it a **Note** field for the selected pair and the buttons **Add pair**,
**Update pair**, **Remove**, **Move up**, **Move down**, **Sort by transit time**, **Load for acquisition** (amber when
usable and no other amber button is: one amber button per view). Double-click a row = Load for acquisition. Empty:
"No pairs yet. Enter a target, pick a reference star, then choose Add pair."

**The editor.** The Target card and the Reference candidates are the editor. Selecting a row loads its target and
reference there (so the sky plots show the selected pair's target). Add pair adds the editor's target (a position is
needed) and its chosen reference (optional). Update pair writes the editor back to the selected row and is enabled
only when they differ. Adding a target already in the list (same name ignoring case and spaces, or within 1′) warns
without blocking.

**Stored** in the settings, `plan.pairs = {"version": 1, "items": [...]}`, plus `plan.selected`. A pair: `id`,
`target` (name, ra_deg, dec_deg, source simbad | manual, vmag, sp_type), `reference` (name, ra_deg, dec_deg, vmag,
sp_type, distance_deg) or null, `note`, `created`. Visible window, Moon separation, transit and status are computed
when shown (a worker thread, `plan-pairs`, after every Observing conditions computation), never stored. The observing
location is never stored with a pair.

**Observing plan.** With planned pairs the Text / JSON output covers all of them: date, window, then one block per
pair (target, reference, RA/Dec, V, visible window, Moon separation, note); never the coordinates of the observing
site. Without pairs it is the editor's target and reference as before. **Open plan…** reads a plan saved as JSON
(`kind: starex-observing-plan`, `version`, `date`, `window`, `pairs`) and asks **Replace** or **Merge** when the list is
not empty (merge skips pairs with the same id, or the same target and reference); the JSON round-trips the pairs
(ids, notes, created time, positions). A one-pair plan from before planned pairs opens too.

## Loading a pair into Acquisition (2026-11-21)

**Load for acquisition** sets, in Acquisition, the **names** of the Target and Reference sets (`name_source` "plan" in
`acquisition_sets.json`) and their **coordinates** with the existing coordinate source control set to the matching
entry: a SIMBAD target → *SIMBAD* (the stored position as the SIMBAD result), a typed target → *Enter by hand*; the
reference → *SIMBAD* (the bundled catalogue's positions come from SIMBAD). A muted "from plan" tag sits after the
coordinates until the user changes the source or edits them. It does **not** set a folder or path. A pair without a
reference sets only the target and leaves the reference as it is.

If Acquisition holds subs or a running watch and the target or reference name differs (a non-empty name that is not
the pair's), it asks first: "Acquisition has 41 subs for beta cas. Load <target> and clear them? Saved stacks stay in
your data folder." **Yes** pauses the watch, runs the existing **Clear all** (no second question), empties both folder
fields (they belonged to the old pair) and then applies the pair. **No** changes nothing. The same pair again, or empty
sets: no question, the folders stay.

The link is kept in the settings as `plan.in_acq` (`id`, `target`, `reference`, `edited`). The Plan table shows "✓ In
Acquisition" on that row while the Acquisition names still match it; Clear all drops the link. The top-row Pair
selector and the reduce flow are unchanged: Select still takes the names from the stack headers and the sets file.
