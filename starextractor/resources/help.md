# Star ExTractor: the whole workflow

Star ExTractor takes you from an observing plan to a calibrated spectrum ready for the AAVSO AVSpec database, for
the Star'Ex LR slit spectrograph with a ZWO ASI585MM Pro camera. This guide walks through one night from start to
finish, then explains the tools around it. Installing, updating, a practice night without a telescope and what to
expect from this version are at the end. The About window (click the logo at the top left) shows the version you
are running.

**The short version**

1. **Plan**: choose the target and a reference star near it; check when they are up; add them as a planned pair.
2. **Acquisition**: watch your capture folder while you image; every sub is checked as it arrives; stack the good
   ones.
3. **Reduce**: *Select* the two stacks, then run *Geometry*, *1D Extract*, *Wavelength* and *Response*.
4. **Export**: *Collect data* (name, observer, site, equipment), *Test*, *Publish* the AVSpec file.
5. **Upload** the AVSpec file yourself on the AAVSO website. Star ExTractor never uploads anything.
6. **Library**: find any night or pair again, read its progress and notes, reopen it or promote it to Published.

The coloured row under the top bar (the *workflow rail*) shows where you are: Acquisition (Capture), Reduce (Select,
Geometry, 1D Extract, Wavelength, Response) and Export (Collect, Test, Publish). A green step with ✓ is done, the
amber one is where you are, grey means not run yet (or out of date), and a red outline means its input is missing.
Click any step to open it.

---

## Before your first night

**Data root.** At the first start Star ExTractor asks where to keep its data (for example
`D:\StarExtractorData`). It creates four folders there:

- `Stacks\<night>\`: the stacks you make in Acquisition and the night's sets file.
- `Masters\<camera>\<year>\`: your master darks, bias and flats.
- `Processed\<night>_<Target>__<Reference>\`: one folder per reduced pair (logbook, 1D spectra, exports).
- `Published\<year>\…`: AVSpec files you promoted as final.

You can change these later in **Settings › Data locations** (the gear at the top right, Ctrl+,). A missing drive is reported at start-up; nothing is
written elsewhere.

**Masters.** Before you stack, add calibration frames to the master library: **Acquisition › Pre Processing
settings › Create masters** (point it at a folder of raw darks, bias or flats) or **Import** finished masters. A
dark is required to stack; bias and flats are optional. A dark without a bias is only used at exactly the same
exposure; with a bias it is scaled to the exposure.

**Your details for AAVSO.** The observer code, site and equipment are typed once in **Export › Collect data** and
remembered in your settings. They are only written into the files you export.

**No telescope at hand?** Create a small synthetic night to practise with: run
`StarExtractor.exe --make-example "<a folder>"`. It writes `target`, `reference`, `darks` and `bias` folders you can
use in every step below.

---

## 1. Plan: what to observe tonight

Open **Plan** in the top row (left of Library). The page has three rows: at the top, the **Target** card and the
**Reference star candidates** on the left, the **Observing conditions** with the sky plot and the altitude chart on
the right; under them the **Planned pairs**; at the bottom the **Observing plan**.

### The target

Type the name (for example `gam Cas` or `V1405 Cas`) and press **Check with SIMBAD** (or Enter). One request goes
to SIMBAD; the line under the field shows what it found: the main identifier, the object type, V magnitude and
spectral type, and RA/Dec fill in. A new nova or transient may not be in SIMBAD yet: then type RA and Dec yourself
(`hh mm ss.s` / `±dd mm ss`, or decimal degrees). The chip next to the position says where it came from: **SIMBAD**
or **Manual**. The small copy buttons copy the bare RA or Dec for your capture program.

### The reference star

**Reference star candidates** lists bright B and A main-sequence stars near the target on the sky, from a list
bundled with the app (offline). Every star has a **grade**: **✓ good**, **ok** or **! poor**; hold the mouse over
the grade for the reasons. By default only good and ok stars are listed, nearest first; the list is sorted by
distance on the sky within the grades shown, never by distance alone. Pick a row and press **Use as reference** (or
double-click it).

- **Include poor-grade stars** (under the table, off by default) adds the poor stars into the same distance order,
  each with its grade; the nearest good-or-ok star then carries a **best** tag.
- When a poor star is closer than the nearest good or ok one, a line under the table says so: *A closer star is
  graded poor: HD n (x.x°): the reasons*.
- **Faintest V** (next to it, 2.0 to 6.5, default 6.5) leaves out stars fainter than that, for a small telescope or
  short exposures. A small **faint** tag marks stars between V 6.0 and 6.5; **✔** marks a star we checked by hand.
- The Name and Sp. type tooltips show E(B−V), v sin i, the distance and how many spectral-type measurements agree.

Altitude and airmass are not taken into account: check them in the sky plots. Emission-line stars are never listed.
The app remembers both settings. The next section says where the list comes from.

### Where the reference stars come from

The list is our own selection, made by fixed rules from public catalogues by a separate program (starex_refcat
**0.1.0**, preset **balanced**). The app never changes it and never goes online for it.

**Sources and dates**
- **SIMBAD** (queried through its TAP service, mirror https://simbad.cfa.harvard.edu/simbad/sim-tap/sync; data
  retrieved 2026-11-24): positions, V and B magnitudes, the adopted spectral type and every other spectral-type
  measurement, object types, rotation (v sin i) and parallaxes.
- **VizieR** (CDS) for two catalogues: the **AAVSO International Variable Star Index (VSX)**, to find variables and
  their amplitudes, and the **Washington Double Star Catalog (WDS)**, to find close companions.
- **Pecaut and Mamajek (2013)**, Table 5: the intrinsic colours of dwarf stars, used to estimate reddening.

**Which stars are in it**
- Stars whose SIMBAD spectral type is B or A, from V 2.0 to V 6.5, over the whole sky. **Normal**: V up to 6.0
  (bright stars included); **faint**: 6.0 to 6.5.
- Main-sequence stars (class V) keep the full grade. Subgiants (IV, IV-V), giants (III, III-IV) and stars without a
  luminosity class are kept with a lower grade when their colour fits their type.

**Never listed** (hard rules)
- a spectral type that cannot be read or has no subclass; not a B or A type; B or V magnitude missing;
- supergiants and bright giants (classes I and II);
- a giant or subgiant whose colour does not fit its type;
- emission-line or shell stars: "e", "(e)", "sh" or "shell" in the type or in any SIMBAD measurement, or a SIMBAD
  emission-line object type;
- chemically peculiar or peculiar spectra (p, m, Am, element codes such as Si or HgMn, λ Boo, weak-lined);
- composite spectra ("+" in the type, or "/" between two spectral types such as B9/A0V);
- spectroscopic, eclipsing and ellipsoidal binaries (SIMBAD object type, a VSX eclipsing type, or "SB" in a
  measurement);
- large-amplitude variables: a VSX amplitude above 0.1 mag, or a Cepheid, RR Lyrae, Mira or other long-period type;
- E(B−V) above 0.15, or below −0.15 (far bluer than the type), or no intrinsic colour for the type.

**Kept with a lower grade** (soft rules)
- giant (III or III-IV): at most poor; subgiant (IV): at most ok; no luminosity class: at most poor;
- uncertain spectral type (":" or "?", a range wider than one subclass, or at least two, or more than half, of the
  MK measurements disagreeing by more than one subclass or one luminosity class): at most poor;
- variable with an amplitude of 0.1 mag or less, or unknown (a SIMBAD variable type, or a VSX variable): at most poor;
- suspected variable or candidate binary or peculiar star (VSX or SIMBAD): at most poor;
- a WDS companion within 6″ and no more than 4 magnitudes fainter, or within 6″ with no magnitude: at most poor;
- E(B−V) from 0.05 to 0.15, or from −0.15 to −0.05 (bluer than the type): at most poor.

**Reddening.** E(B−V) is the star's B−V from SIMBAD minus the B−V a dwarf of its spectral type has (Pecaut and
Mamajek 2013, interpolated between listed subclasses). Giants and subgiants use the dwarf colours too, which is why
one whose colour does not fit is left out.

**Grades.** A star gets the lowest grade any of its reasons allows.
- **good**: A0 to A3, class V, no broad-line code (n or nn), E(B−V) at most 0.03, v sin i at most 150 km/s (when
  known), within 300 pc, and at least two MK spectral-type measurements that agree, or one agreeing type from an
  expert MK survey (for example Cowley et al. 1969, Gray and Garrison, Abt and Morrell 1995, Osawa 1959); and none
  of the soft rules above.
- **ok** at best: B8 to B9.5 or A4 to A5; class IV-V or IV; broad lines (n); v sin i 150 to 250 km/s; E(B−V) 0.03
  to 0.05; fewer agreeing type measurements than good needs; farther than 300 pc; no parallax.
- **poor**: earlier than B8 or later than A5; very broad lines (nn); v sin i above 250 km/s; no MK classification;
  any soft rule except subgiant.

**What is in this version**: 1595 stars: 106 good, 351 ok and 1138 poor (871 normal, 724 faint; none verified by
hand yet). 99.3 % of the sky has a good or ok star within 15° (V 6.5 or brighter); counting only stars above 30°
altitude, 98.1 % to 99.8 % for observers at latitudes from +60° to −60°. The largest gap is 17.6° to the nearest good
or ok star (around RA 12h, Dec +37°), with a poor star 3.5° away.

**Assumptions and limits**
- SIMBAD's spectral types come from many catalogues of very different ages and quality; the rules read what is
  written there, not what the star is.
- **good** means a likely match to the library spectrum of the same type, not a guarantee.
- The list is sorted by distance on the sky, not by altitude or airmass.
- The grades have not yet been checked against our own observations.
- Stars brighter than V 4 are included on purpose: shorten the exposure time.
- The list can change between versions of the app; it is regenerated outside the app.

### Observing conditions: when is it up?

The **Observing conditions** card shows when the target is above your horizon tonight.

- **Location.** Latitude and longitude come from your saved site (Export), else from your stack headers. Type your
  own (decimal degrees such as `30.67` and `-104.02`, a direction such as `104.02 W`, or `104 01 21 W`), or press
  **↻ Use my location** for an approximate position from your internet address (the only internet request on this
  card, and only when you click it). **Reset to site default** goes back to the app's site. The location is used
  for planning only: it stays in your settings and is never written into a data file or the observing plan.
- **Date, From, To.** The evening the night starts and the window you will observe, in your computer's time zone
  (the note under the fields names it). **Astronomical night** sets the window from the end of evening twilight to
  the start of morning twilight (Sun at −18°).
- **Min altitude.** Below this the target is too low (default 25°).

The **polar sky plot** (left) is the sky seen from above: north at the top, east on the right, the zenith in the
middle and the horizon at the edge, rings at 30° and 60°, your minimum altitude as a dashed red ring.

- The target's path through the window is a **solid line**; an amber dot marks where it rises above or sets below
  your minimum altitude (with the time in the legend), a bright dot its position now.
- The **Moon** is a **dashed line** over the same time range, and a **hollow ring** shows where it is now (or at the
  middle of your window when now is outside it). When the Moon stays below the horizon the legend says so.

The **altitude chart** (right) shows altitude against local time over your window ± 2 hours: the target (thick
solid line), the Moon (dashed), your minimum altitude (dotted) and now (thin line). The background shading is
daylight and the three twilights. Under both, one line sums it up, for example *Above 25° from 20:00 to 22:46 ·
transit 18:12 at 72° · Moon 63° away, 71 % lit*.

### Planned pairs: a list for the night

Most nights you observe more than one target. The **Planned pairs** card keeps a list of target + reference pairs.
(It is not the same as the pair chip in the top row: that one is the pair loaded for reduction.)

- **Add pair** adds the target and the reference star chosen above as a new row. A reference is optional at this
  point: the row then shows an amber **! pick reference**. Adding a target that is already listed warns you but adds
  it anyway. The **Status** column says **● In Acquisition** for the pair loaded there, **Ready** for a pair with its
  reference star, and **! pick reference** for one without.
- **Select a row** to load it into the Target card and the reference list above; the sky plots then show that
  target. Change anything there and **Update pair** writes it back into the selected row.
- **Remove**, **Move up**, **Move down** and **Sort by transit time** (needs your location) arrange the list.
- **Note**: a free-text note for the selected pair (exposure ideas, the order, a reminder).
- Each row shows when the target is **Visible** in your window (for example `20:00–22:46`, `not above 25°` or
  `never rises`) and its **Moon separation**. They are recalculated whenever the conditions change.
- The list is kept in your settings and is there again the next time you start the app.

### The observing plan

The **Observing plan** card at the bottom turns the list into text or JSON: the date, your window and one block per
planned pair (target, reference, RA/Dec, V, visible window, Moon separation, note). Never the coordinates of your
site. **Copy** puts it on the clipboard, **Save…** writes a file. Save it as JSON to bring the same list back later
with **Open plan…** (you are asked whether to replace the list or merge the new pairs into it).

### Loading a pair into Acquisition

Select a planned pair and press **Load for acquisition** (or double-click the row). In Acquisition this sets the
**names** and **coordinates** of the Target and Reference sets, tagged *from plan*. It does **not** set any folder:
you still choose the capture folders there. A pair without a reference sets only the target.

If Acquisition already holds subs (or is watching a folder) for a **different** target or reference, you are asked
first, for example *Acquisition has 41 subs for beta cas. Load V1405 Cas and clear them?* **Yes** pauses watching,
clears the collected subs and empties the folder fields (they belonged to the old pair); your saved stacks stay in
your data folder. **No** changes nothing. Loading the same pair again asks nothing.

---

## 2. Acquisition: imaging and stacking

Open **Acquisition** (the first group of the rail, *Capture*). It watches the folder your capture program (SharpCap,
N.I.N.A.) saves to and checks every new sub while you image.

### Target and Reference sets

The two tabs **Target** and **Reference** above the box are the two sets of the night. The tab you are on decides
which set new subs join: the FITS header never decides this. Switch tabs when you move the telescope to the other
star. When a planned pair was loaded, a slim strip at the top reads *Planned pair: V1405 Cas + HR 9071 (from
Plan)*; **Change in Plan** jumps back to that row. If you edit the names here, the strip says *edited*.

For each set:

- **Folder** + **Browse…**: the folder the subs arrive in. The folder picker also shows the FITS files of a folder
  (greyed) so you can see it is the right one; you can only choose folders.
- **Start watching** / **Pause**: one set is watched at a time, every few seconds (set *every N s*). Tick **Process
  existing files** to also load what is already in the folder (for older nights).
- **Name** + **SIMBAD** (resolve and fill the coordinates) or **Header** (take OBJECT from the first file).
- **Coordinates from** SIMBAD | From header | Enter by hand. The coordinates are used for the airmass correction in
  Response.
- **Clear all** forgets every sub, both sets, the rows and the names of this session (your image files are never
  touched).

### What you see for each sub

- **Sub** card, **ADU profile**: the brightest column values along the spectrum against the linearity limit. The
  dashed line is the limit; shaded columns are saturated. **Sub image**: the frame straightened along the tilt with
  the spectrum (blue) and sky (brown) rows that were used. Drag a band to correct the rows, or zoom in on the star
  and press **Re-detect**. The rows are found automatically on the first sub of each set and then locked for that
  set. A defocused, two-lobed star image is recognised and the spectrum band spans both lobes.
- **Recommendation**: how full the brightest pixel is, the longest safe exposure, and how many more subs you need
  to reach your target SNR.
- **Good subs**: every sub of the set with exposure, peak, SNR and flags; untick one to leave it out, right-click
  to move it to the other set or exclude it. Click a sub to show it.
- **Stack SNR**: the SNR of the stack so far, the √N expectation and a projection; set your **Target SNR** (default
  100) in its header.
- **Notes for this set**: a free-text logbook, saved with the night; it goes into the pair's logbook later.

### Stacking

When a set has enough good subs, press **Stack selected**. The ticked subs are calibrated with your masters
(dark, optional bias and flat; the calibration used is named) and stacked into
`Stacks\<night>\<Name>_target_S.fit` or `<Name>_ref_S.fit`. The stack carries the rows and names, so Reduce finds
them by itself. **Pre Processing settings** (the tab at the right) holds the master library, master tolerances and
the stacking options (method, sigma clipping, alignment, pedestal and gain matching).

---

## 3. Reduce: from two stacks to a calibrated spectrum

### Select

Press **Use Acquisition stacks** to fill both boxes with the target and reference you stacked last in Acquisition
(it does not load them), or **Browse…** to any two stacks, also ones made by other programs (Browse starts in the most
recent night of the Stacks folder). If only one set is stacked, the other box is left as it is and the page says so;
if nothing is recorded yet, it offers the stacks Acquisition wrote in the most recent night of the Stacks folder, to
confirm with **Use these**. After stacking both sets, Acquisition shows **Both stacks are ready** with **Open in
Reduce**, which does the same and puts the focus on **Load pair**. Each card shows the name (from the file header, the sets file or typed), the exposure, the camera
settings, the position and the rows, with a preview of the spectrum band. The **Checks** card lists what is fine (✓),
what to look at (!) and what must be fixed (✗); only ✗ blocks. Type **Pair notes** if you like, then press **Load
pair**: the pair folder is created under Processed and every later step works on this pair.

You do not have to load the pair you planned: observing another target or reference than the Plan says is fine,
and Select does not compare them. The notes under each Reduce step header only point out what affects the result:
a reference without a spectral type or a poor flux reference, a name that differs from the stack's OBJECT, and
missing coordinates or site position for the airmass correction.

### Geometry

Straightens the spectrum so it runs exactly along the rows. **Detect** measures the tilt, or **Draw angle**: press
at one end of the spectrum, drag along it and release. *Slant* (optional) corrects lines that lean across the slit.
**Use saved geometry** reuses the spectrograph's saved values (they rarely change). *Original | Corrected* compares
the result; the blue *Level line* helps to judge it. A tilt found on the target is offered to the reference as a
first guess.

### 1D Extract

Turns the 2D stack into a 1D spectrum. The blue band is the spectrum, the two brown bands the sky either side.
**Auto-place** finds the brightest spectrum (in the rows in view, so zoom in to pick a faint star next to a bright
one); drag the band edges to adjust. The sky is the mean of Sky A and Sky B and is subtracted. **Copy bands to the
reference stack** reuses the bands, and **Save as default rows** keeps them for next time.

### Wavelength

Gives every pixel a wavelength. The reference star's Balmer lines (Hε … Hα) are found by their spacing, without
assuming where they are; the solution saved from an earlier night is only a guide, and the *Solution* card says when
it does not fit tonight (the camera may have moved). Drag a marker onto its line if needed: the fit follows from
wherever you drop it, and a marker stops 15 px before its neighbours, so the lines always stay in wavelength order. In
the table, **FIT** switches a dragged line to "mine" (your position, no fit). Add or remove lines (Hζ and Hη are
optional extras for A-type stars), choose **Linear**, **Quadratic** or **Cubic** (never more terms than the lines allow: Cubic needs 5 lines, and is never chosen for you). The
table shows each line's centre uncertainty (±), width, error and flags (⚠ blend or emission, ⊘ left out); every RMS
says which fit it belongs to ("Free fit", "Shift only"). A fit whose RMS is too large is **failed**, not passed on.
The **Coefficients** box gives the solution in the BASS format (nm, offset first) to copy or paste. The target gets
its own solution: the reference's, plus a shift measured on its sky lines and telluric bands (**Auto-match**), or
fitted freely on the target's own lines; a manual target shift is reset when the reference solution changes.
*Resolution* measures the FWHM and R on Hβ.

### Response

Corrects the instrument's sensitivity. The observed reference is divided by a library spectrum of its spectral type
(for example A0V Pickles) to get the raw response; the smoothed (pink) curve is the one applied. Mask the Balmer
lines and telluric bands, use the airmass correction (it needs the coordinates and the site), and let **Follow the
upper envelope** ignore absorption dips. **Normalise and crop** sets the normalisation range and the wavelength
range kept. The bottom chart shows the corrected target and reference. **Save response** keeps a good response for
nights without a reference.

---

## 4. Export: AVSpec for AAVSO

### Collect data

Check the **Object name** (the VSX name; **Check VSX name** asks VSX once and **Use VSX name** applies it), the
spectrum to publish (zoom on the Balmer lines, Hα, the telluric bands), and fill in **Observer** (your AAVSO
obscode), **Location** (site name, latitude, longitude, elevation: from the frame headers by default) and
**Equipment**. Tick *Remember for future sessions* once. The FITS header preview shows what will be written.

### Test

Checks every AVSpec requirement and writes the file to a temporary folder and reads it back. Items marked ! can be
acknowledged; ✗ must be fixed in Collect data. **Run test again** after a change.

### Publish

**Save AVSpec file** writes the file and the process log into the pair's `export` folder (also FITS, CSV and PNG if
you like). **Promote to Published** copies it to `Published\<year>\…` as version 1 (later **Promote as v2** …).
Then upload the AVSpec file yourself on the AAVSO AVSpec website.

---

## 5. Library: every night and pair

The **Library** (far right in the top row) lists every night and pair under your data root, newest first, with
three progress dots (stacked, reduced, published) and the latest note. The summary cards count nights, pairs,
published spectra and pairs that **need attention** (a stack without a reference, QC warnings, a failed or
out-of-date step, a published file that is gone). Search with Ctrl+F or the filter chips. Select a pair to see its
progress report, its spectrum and notes; **Open in Reduce** continues where you left off, **Promote to Published**
publishes it. A target stack without a pair can be paired here with **Pick a reference**.

---

## Night mode

The moon switch in the top row (or Ctrl+Shift+N) turns on a red-filter safe palette: dark and warm, without green
or blue light; images use a black-to-amber map. In the night palettes, states are shown by brightness and symbols
(✓, !, ✗), never by colour alone. For a pure red palette set `ui.palette` to `red` in `settings.json`. The splash
screen is dimmed in night mode.

## Keyboard

| Keys | Action |
|---|---|
| F1 | This help |
| Ctrl+, | Settings |
| Ctrl+Shift+N | Night mode on/off |
| Ctrl+1 … Ctrl+5 | Reduce steps 1 to 5 |
| Alt+← / Alt+→ | Previous / next step |
| Ctrl+PgUp / Ctrl+PgDn | Previous / next tab |
| F5 | Run the current step |
| Esc | Stop |
| Ctrl+O | Open a pair |
| Ctrl+F | Search (Library) |

## Where your files are

- Your **data root**: Stacks, Masters, Processed and Published (see *Before your first night*).
- `%APPDATA%\StarExtractor\`: settings (including the planned pairs and your planning location), the instrument
  profile, saved responses, the VSX cache and `error.log`.
- Nothing is written into the program folder.

## Internet and privacy

Star ExTractor works offline. It goes online only when you click: **Check with SIMBAD** (Plan, Acquisition),
**Check VSX name** (Export), geocoding the site (Export), **Use my location** (Plan) and **Send** in Help › User
Feedback. Your planning location is never written into a data file, a FITS header, an export or the observing plan.

## User Feedback

The **User Feedback** tab in this Help window sends a message to the developer: a problem, an idea, a question or
anything else. Choose the type, write your message (up to 5000 characters), add your email if you would like a reply,
and press **Send** (or Ctrl+Enter). A reference such as FB-A1B2C3 comes back; quote it if you write again.

**What is sent**: your message, your optional email, the app version, the Windows version and, if you tick **Attach
the application log** (on by default for problems), the log of the open pair and the end of the error log. Before
anything is shown or sent, folder paths, your Windows user name, your computer name and email addresses inside the
log are removed. **See exactly what is sent** shows the exact text. No location, no file paths and no images are sent.

**Privacy**: the message goes to the developer's private feedback inbox. Your email is used only to reply. Feedback
and emails are kept for 12 months. To have yours deleted, send a new message of type **Question** and quote your
reference. Nothing goes online until you press Send, and your draft is not saved on disk.

**When sending fails** (no internet, or the service is busy), the tab offers to save the message as a zip file
(`starex-feedback-<date>.zip`, in Documents by default) with the message, the log and a short README. Keep it and send
a new message from this tab when you are online. **Save as file…** makes the same zip at any time.

## When something goes wrong

- **An error window**: press **Open log folder**; `error.log` has the details. Send a message from Help › **User Feedback** with **Attach
  the application log** ticked (it includes the end of `error.log`).
- **Check the installation**: run `StarExtractor.exe --selftest`; it reduces a small example without a window and
  prints `self-test PASSED`.
- **A step is grey after you changed an earlier one**: that is on purpose; changing a step makes every later step
  out of date. Run them again.
- **The spectrum rows are wrong** (Acquisition or 1D Extract): zoom in on the star you want and press Re-detect /
  Auto-place, or drag the bands.
- **The wavelength fit is poor or failed**: check that each marker sits on its line in the reference profile (a grey
  marker with "?" found no line: hover it for the reason); remove lines you cannot see; prefer Linear with few lines.
  On some nights a quadratic cannot follow the spectrograph better than about ±1 Å.
- **No reference star tonight**: use a saved response in Response.
- **Start fresh**: close the app and rename `%APPDATA%\StarExtractor`; your data root is not touched.

## Installing and updating

Download `StarExtractor-Setup-<version>.exe` from the project's Releases page on GitHub
(github.com/MBoerlage/Star-ExTractor) and run it. It installs for your user account, so no administrator rights are
needed. Windows may show **"Windows protected your PC"** because the program is not code-signed: click **More
info**, check the file name, then **Run anyway**. A portable zip is there too: unzip it anywhere and run
`StarExtractor.exe`.

To **update**, run the newer installer over the old one: your settings, master library and data stay. To
**uninstall**, use *Settings → Apps* in Windows; your data root is never removed.

## Trying it without a telescope

Star ExTractor can write a small synthetic night (target, reference, darks and bias) to try every step with. Press
*Win+R* and run

`"%LOCALAPPDATA%\Programs\Star ExTractor\StarExtractor.exe" --make-example "%USERPROFILE%\Documents\StarExtractor example"`

(use your own install folder if you chose another one). Then point Acquisition at the `target` and `reference`
folders with **Process existing files**, add the darks and bias in *Pre Processing settings*, stack, and carry on
with Reduce and Export as described below.

## What to expect in this version

Star ExTractor supports one instrument (the Star'Ex LR with a ZWO ASI585MM Pro) on Windows. *Run all* (every Reduce
step in one go) is hidden for now: run the steps one by one. Uploading to AVSpec is done by you on the AAVSO website.

Found a problem, or have an idea? Send it from the **User Feedback** tab in this Help window; tick **Attach the
application log** if there was an error window. The About window (click the logo at the top left) shows the version
you are running.
