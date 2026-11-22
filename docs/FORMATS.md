# Output formats: AAVSO AVSpec and ARAS

**Scope (Matt, 16 Nov 2026):** the app publishes to **AAVSO AVSpec and ARAS only. No BeSS database export, card, button or BeSS observer code anywhere in the UI.** The ARAS file still uses the BeSS FITS header convention (`BSS_*` keywords), because ARAS accepts reduced spectra only in FITS/BeSS format; that is a file format, not a BeSS submission.

Status key: **VERIFIED** = confirmed from AAVSO/ARAS/BAA pages found during design (Nov 2026). **VERIFY** = not yet confirmed; Claude Code must fetch the source and record the result here before relying on it.

## Common output
A **1D, wavelength-calibrated, scaled** FITS: one primary HDU, 1D float32 data, linear wavelength axis described by `CRVAL1`, `CDELT1`, `CRPIX1=1`, `CTYPE1='Wavelength'`, `CUNIT1='Angstrom'` (these four appear in a header accepted by AAVSO from ISIS). Write them with astropy; keep header cards short and ASCII.

## AAVSO AVSpec (research)
Primary source, **now in the project**: `AAVSO_technical_guide_v1_4.pdf` (kept next to the project, not in the repository) (AAVSO Spectral Database Users Manual v1.4, 15 pages; text copy in `docs_txt\aavso_technical_guide_v1_4.txt`). Other pages: quick guide https://apps.aavso.org/media//specdb/aavso_spectra_quickguide.pdf, submit page https://aavso.org/apps/avspec/submit, help https://apps.aavso.org/avspec/help.

### Data reduction rules (VERIFIED, manual §3)
- 1D spectrum, **dark-subtracted** (bias and flat optional but strongly recommended), **wavelength calibrated**, wavelength unit **"Angstrom"** (CUNIT1).
- **Do NOT correct for heliocentric or Doppler velocity.** **Telluric features must stay in the spectrum** (validators use them to check the calibration). The Export tab must therefore never apply a velocity shift or telluric removal to the published file (BSS_VHEL = 0).
- "Scaled" (response-corrected, normalised) spectra are what the quick guide asks for.
- One file = one observation of one star; no multi-dimensional files. Several files may be uploaded at once for the same site/equipment pair. `.zip` only for echelle.
- First-time submitters: upload one spectrum of a **standard star** (selected in the form) so AAVSO can check that header and profile agree; validation can take days. After approval uploads are free. Duplicate rule: same observer, equipment, site and star with the same rounded mid-observation JD.

### Mandatory keywords (VERIFIED, manual §3; alternatives in brackets are accepted and renamed by AVSpec)
| Keyword | Meaning / rule |
|---|---|
| `SIMPLE`, `BITPIX`, `NAXIS`, `NAXIS1` | standard FITS (1D: NAXIS=1) |
| `CRVAL1`, `CDELT1`, `CRPIX1` | linear wavelength axis: value at reference pixel, step, reference pixel |
| `CTYPE1` | axis type (use `'Wavelength'`) |
| `CUNIT1` | **must be `'Angstrom'`** |
| `OBJNAME` (alt `OBJECT`) | object name; **must be recognised by the Variable Star Index (VSX)** |
| `DATE-OBS`, `DATE-END`, `EXPTIME` | **ISOT format** (`2017-01-01T00:00:00.000`), at least 2 of the 3 present; the third is computed. Other formats are rejected. Write all three. |
| `OBSERVER` | **AAVSO obscode, must match the user's login obscode** (AAVSO can translate a registered alias; not needed here) |
| `AAV_ORD` | echelle only; not needed |

### Recommended keywords (VERIFIED)
`EXPTIME2` (exposure decomposition, e.g. "10 x 180 s"), `JD` (**mid-exposure** JD, not start; computed from DATE-OBS/DATE-END if absent), `AAV_INST` (alt `BSS_INST`, `INSTRUME`), `AAV_SITE` (alt `BSS_SITE`, `SITE`), `AAV_ITRP` (resolving power; alt `BSS_ITRP`, `ITRP`), `VERSION` (software; alt `SWCREATE`).
- If site and instrument are not in the header, AVSpec adopts the ones chosen in the upload form. The header names should match the **site nickname** and **equipment package nickname** exactly (equipment type must be "Spectroscopy").
- Header keys not in the standard are kept; renamed alias keys get a HISTORY card. AVSpec adds VSX data (RA/Dec, type, period) to the header itself.

### Automatic checks the tool should mimic (VERIFIED, manual §5)
1. Required keywords present with correct types (string / int / float).
2. ISOT time format; at least two of DATE-OBS, DATE-END, EXPTIME.
3. CUNIT1 = Angstrom.
4. VSX cross-match of the object name. **Decided: a "Check VSX name" button in the Export tab does an online lookup, one request per click, never automatic and never in a loop** (Matt has been throttled by AAVSO before). **This is the only place VSX is used; everywhere else the tool uses SIMBAD or manual entry only.** The OBJNAME field defaults to the target name from Files but is editable, because the VSX name can differ from the SIMBAD name. The VSX API endpoint is not in the manual: Claude Code must **VERIFY** it (from the AAVSO VSX pages; the design session could not fetch it, the host was not on its allowlist). Offline or failed lookup: show "Could not reach VSX" with a link to check by hand, and do not block export.
5. Obscode, site and equipment cross-checked against the upload form.
6. **Airmass at object coordinates, mid-JD and site must be between 1 and 5**, otherwise an error. The tool computes the airmass from the object RA/Dec (SIMBAD/header/manual), site lat/long and JD and shows a warning if it is outside 1–5. This also needs correct site coordinates.
7. Data section opens and is not empty. Users should describe their reduction in the header (add HISTORY cards: stack, calibration, response library, airmass correction).

### Telluric list (VERIFIED, manual appendix A.1)
The AAVSO validation team checks wavelength calibration against telluric lines: H2O near Hα 6532–6587 Å, O2 B band 6867–6924 Å, O2 A band 7594–7697 Å, and bandheads O2 a 6278.3, H2O 7168, 8230, 8935, 9275 Å. All 67 values are in `starex_reduce/data/aavso_telluric_lines.csv`. Use them for the Export tab telluric shading instead of the rough ranges in the earlier mockup (which showed O₂ γ 6276–6290, O₂ B 6867–6886, H₂O 7160–7340). Note the **H2O lines near Hα (6532–6587 Å) sit right on top of Hα**, which is useful for judging the Hα alignment.
- **Decided by Matt: the publishing crop keeps the O2 A band: default 3900–7700 Å** (the calibration reaches about 8110 Å). The Export tab shows these AAVSO telluric lines as a visual quality check: bands (H2O near Hα, O2 B, O2 A), bandheads (O2 a 6278.3, H2O 7168) and one tick per listed line.

### Still to VERIFY (not in the manual)
- Exact accepted spelling/value of `CTYPE1` (manual lists it as mandatory with no value; ISIS uses `'Wavelength'`).
- `BSS_VHEL`, `BSS_COSM`, `BSS_TELL`, `BSS_NORM`, `Q_CAL`: not required by AVSpec; see the ARAS section.
- The AAVSO standard star list (https://aavso.org/apps/avspec/standardstars).

## ARAS
Scope: file for the ARAS spectral database. No BeSS submission.

VERIFIED (ARAS forum and BeSS/BAA pages found during design):
- ARAS asks for **reduced spectra in FITS/BeSS format only, with a filename convention** (ARAS forum post by Olivier Thizy, https://www.spectro-aras.com/forum/viewtopic.php?p=7003). The BeSS FITS header standard is therefore the ARAS file format; that is why `BSS_*` keywords appear in the file.
- `BSS_ESRP` = real spectral resolution measured in the spectrum; `BSS_SRPW` = wavelength used for it (ARAS forum thread https://spectro-aras.com/forum/viewtopic.php?p=17624).
- Example cards in an ISIS-written file accepted by AAVSO: `OBJNAME`, `DATE-OBS`, `EXPTIME`, `EXPTIME2`, `BSS_INST`, `BSS_SITE`, `OBSERVER`, `CUNIT1`, `CTYPE1`, `CRVAL1`, `CDELT1`, `CRPIX1`, `BSS_VHEL`, `BSS_COSM`, `BSS_TELL`, `BSS_NORM`, `Q_CAL`, `VERSION`.
- Submission route: older ARAS posts say spectra are sent by email to the database maintainers (https://spectro-aras.com/forum/viewtopic.php?p=15482); a newer online database exists at https://aras-database.github.io/database/ . **VERIFY the current route** before the ARAS button does more than prepare the file and open its folder. Do not claim upload support.

VERIFY: the BeSS standard document (http://basebe.obspm.fr/basebe/Spec_spectres_BeSS_en.pdf, linked from the BAA requirements page) for required vs optional keywords, `Q_CAL`, `BSS_VHEL`, `BSS_COSM`, `BSS_TELL`, `BSS_NORM` allowed values, site latitude/longitude/elevation keywords; and the **ARAS file naming convention**. Never guess keyword names.

## Header written by this app (draft)
Write both the AAVSO and the `BSS_*` names for site/instrument so one file serves both databases (AVSpec reads `AAV_*` first and maps `BSS_*`; ARAS uses the `BSS_*` header convention). Keep every card ASCII and short.
```
SIMPLE/BITPIX/NAXIS/NAXIS1 as standard (1D float32)
OBJNAME  = '<target>'                       (must exist in VSX for AVSpec)
DATE-OBS = '2026-08-20T02:14:09.000'        (UTC, ISOT; start of first sub)
DATE-END = '<ISOT end of last sub>'
EXPTIME  = <total s>      EXPTIME2 = '<n> x <s> s'
JD       = <mid-exposure JD>
OBSERVER = '<AAVSO obscode>'
AAV_SITE = '<site nickname>'     BSS_SITE = '<same>'
AAV_INST = '<equipment nickname>' BSS_INST = '<same>'
AAV_ITRP = <R measured>   BSS_ESRP = <R measured>   BSS_SRPW = <wavelength Angstrom>
CTYPE1 = 'Wavelength'  CUNIT1 = 'Angstrom'  CRVAL1 = <A>  CDELT1 = <A/px>  CRPIX1 = 1
BSS_VHEL = 0   (no velocity correction; AAVSO wants none)
VERSION  = 'Star ExTractor <ver>'
HISTORY  ... stack, dark/bias used, geometry, wavelength solution, response library, airmass correction
```
Latitude/longitude/elevation keywords for ARAS: **VERIFY**; do not guess names. `BSS_COSM`, `BSS_TELL`, `BSS_NORM`: **VERIFY** values.

## Pre-flight checks shown on the Export cards
**AVSpec:** 1D calibrated, scaled, wavelength unit Angstrom; OBSERVER holds the obscode; DATE-OBS/DATE-END/EXPTIME in ISOT; object name checked in VSX with the **Check VSX name** button; site and instrument nicknames filled (must match the AAVSO records); airmass 1–5 computed; no velocity correction and telluric features kept; first-submission note (standard star).
**ARAS:** 1D FITS in the ARAS (BeSS-style) format written; BSS_ESRP/BSS_SRPW recorded; observer, site, instrument filled; file name follows the ARAS convention (VERIFY the convention).

## M6 verification results (2026-11-16)
What the app writes and checks now, and what is still open. Sources: the local AAVSO manual v1.4 text (`docs_txt/aavso_technical_guide_v1_4.txt`) and the accepted submission (`example_spectra/novasrg2026/submitted/`).

- **AVSpec keywords — VERIFIED (manual §3):** mandatory OBJNAME (alias OBJECT), DATE-OBS, DATE-END, EXPTIME (ISOT, ≥ 2 of 3), CUNIT1 = 'Angstrom', CTYPE1, CRVAL1, CDELT1, CRPIX1, OBSERVER = obscode; recommended EXPTIME2, JD (mid-exposure), AAV_INST (alias BSS_INST, INSTRUME), AAV_SITE (alias BSS_SITE, SITE), AAV_ITRP (alias BSS_ITRP, ITRP), VERSION (alias SWCREATE). AVSpec searches AAV_* first, then the aliases. The app writes all of them (OBJNAME and OBJECT identical; AAV_* and BSS_* identical).
- **CTYPE1 — RESOLVED:** 'Wavelength' with CUNIT1 'Angstrom' was accepted (submitted file).
- **JD:** written as mid-exposure; on the nova it matches BASS's JD-MID to 1e-5 d.
- **Airmass 1–5 — VERIFIED (manual §5):** computed offline from site, target coordinates and mid-JD (Kasten & Young); shown as a pre-flight check; written as AIRMASS.
- **File name — NO RULE in the AAVSO manual v1.4.** Default pattern from the accepted file: `_<object lowercase, no spaces>_<UT yyyymmdd>_<thousandths of the UT day at start>_<observer>.fit`; the observer part defaults to the obscode.
- **VSX endpoint — (superseded, see the VSX update below) NOT VERIFIED at M6.** AAVSO's documentation page (https://www.aavso.org/direct-web-services-vsx) refused automated access (HTTP 403) and no other official page described the object query. The app therefore does not guess: `settings.json → "vsx_api_url"` (URL template with `{name}`) is empty by default, and **Check VSX name** then opens https://vsx.aavso.org/ in the browser for a manual check (one click = one page). Matt: paste the documented endpoint there once confirmed.
- **BeSS standard — NOT VERIFIED.** http://basebe.obspm.fr/basebe/Spec_spectres_BeSS_en.pdf could not be reached (connection refused). The app writes only the BSS_* keywords present in the AAVSO-accepted file with the meaning AVSpec gives them: BSS_INST, BSS_SITE, BSS_ESRP = BSS_ITRP = resolving-power setting (default 500), BSS_VHEL = 0, BSS_TELL = 'none'. **Not written:** BSS_COSM, BSS_NORM, Q_CAL, BSS_SRPW (only meaningful with a measured R), and any site-position keyword.
- **ARAS — upload skipped (Matt, 2026-11-16).** "Prepare ARAS file" writes the same header to `export/aras/` under the provisional (AVSpec-style) name; the ARAS naming convention and route remain open.
- **AAVSO standard-star list:** not fetched; the pre-flight only reminds first-time submitters.

### Update (2026-11-16, after M6 review)
- **ARAS removed** from the app (Matt): no ARAS card, no ARAS file. The export writes AAV_* keys only; BSS_* keys inherited from earlier steps are dropped.
- **VSX check optional:** OBJNAME follows the Select target name until edited on the Export tab; the pre-flight shows the VSX check as information, never as a blocker. The button never opens a web page.
- **No web pages opened:** "Prepare AVSpec file" writes the FITS file and `<name>_log.txt` (the session's process log) side by side in `<session>\export\`; "Open export folder" shows them. Uploading is done by the observer on the AVSpec website.

### Update (2026-11-16, VSX lookup automated)
- **VSX endpoint — VERIFIED live 2026-11-16** with three single requests (urllib, honest User-Agent `star-ex-tractor/0.1`):
  `https://vsx.aavso.org/index.php?view=api.object&ident=<name>&format=json` (an unofficial interface, so the template stays configurable in `settings.json → "vsx_api_url"`; empty = this default).
  - `SS Cyg` → `{"VSXObject":{"Name":"SS Cyg","AUID":"000-BCP-220","RA2000":"325.67833","Declination2000":"43.58608","VariabilityType":"UGSS","Period":"0.2751300","MaxMag":"7.7 V","MinMag":"12.4 V","SpectralType":"K5V+pec(UG)",…}}`
  - unknown name → `{"VSXObject":[]}` (HTTP 200)
  - `V8055 Sgr` → Name `V8055 Sgr`, AUID `000-BQQ-461`, RA2000 276.30313, Dec2000 −22.42486, type `NA`, range 8.1 V – 21.6: V.
  The earlier 403 was the documentation page on www.aavso.org, not the API.
- **Behaviour:** **Check VSX name** = one request per click, run in a worker, timeout 10 s; successful answers cached in `%APPDATA%\starex_reduce\vsx_cache.json` (case-insensitive name). The result line shows the VSX name, AUID, type, period, range, spectral type and J2000 position. A list answer with several objects is reported as "several match" and nothing is chosen. Network errors and timeouts say "Could not reach VSX"; a non-JSON answer says "unexpected format".
- **Nothing is applied automatically.** **Use VSX name** writes the VSX name to OBJNAME and, only when the target has no coordinates yet, the VSX J2000 position to the target coordinates (logged). The lookup module (`core/vsx.py`) only returns data; FITS keyword mapping stays in `core/export.py`.
- **Site position:** taken from the frames' SITELAT/SITELONG/SITEELEV by default (also for the response airmass); the saved position is the fallback; typing a position on the Export tab overrides the headers until **From frame headers** is pressed.
