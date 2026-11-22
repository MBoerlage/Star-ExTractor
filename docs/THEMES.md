# Themes and night mode (2026-11-19)

The app has three palettes. A switch in the top row (moon + toggle, immediately left of ⚙ Settings; Ctrl+Shift+N;
Space when it has focus) turns night mode on and off:

| ui.palette | What it is | How to choose it |
|---|---|---|
| `default` | The look of the approved mockups (unchanged) | Switch off |
| `night` | Red-filter safe: dark, warm, no green or blue | Switch on |
| `red` | Pure red: only the red channel | Only in `settings.json` for now (`"ui": {"palette": "red"}`); a Settings dropdown will offer it later. The switch turns it off (to default). |

The choice is stored in `%APPDATA%\StarExtractor\settings.json` under `ui.palette` and applied in `app.py` before any
window exists (also before the first-run dialog), so nothing shows in the wrong palette at start. Switching is live
(`ui/retheme.py`): no restart, no recomputation, one log line "Night mode on" / "Night mode off".

This replaces the decision of 2026-11-17 ("no red night palette, no night-mode toggle"), reversed by Matt on
2026-11-19.

## Tokens

Every colour of the UI comes from `starextractor/ui/theme.py` (`PALETTES`). The module attributes (`theme.BG`,
`theme.AMBER`, …) hold the active palette; `theme.token("accent")` reads a token by its name below.

| Token (this doc) | Attribute | default | night | red |
|---|---|---|---|---|
| bg | BG | #0E1116 | #120A06 | #0A0000 |
| panel | PANEL | #12161C | #1C110B | #150000 |
| panel2 | CARD | #161A20 | #26170E | #1F0000 |
| border | LINE | #2A313B | #4F2D1A | #4A0000 |
| border_strong | BORDER | #3A424E | #80502D | #7A0000 |
| text | TEXT | #E8EBF0 | #FBE3C8 | #FB0000 |
| text_muted | MUTED | #A0A9B6 | #F4AA7A | #F40000 |
| accent | AMBER | #F2A33A | #FF9A2E | #FF0000 |
| accent_text | AMBER_TEXT | #1A1206 | #1A0C00 | #120000 |
| done | DONE_BG | #1E4D31 | #6E3E1E | #6A0000 |
| done_text | DONE_TEXT | #7BD389 | #FFD9AE | #FC0000 |
| not_run | NOTRUN_BG | #1B2028 | #24160D | #1A0000 |
| not_run_text | NOTRUN_TEXT | #A0A9B6 | #C8936A | #C00000 |
| missing | MISSING | #FF7B72 | #FF5C3A | #FD0000 |
| ok | OK | #7BD389 | #F4BA7A | #F90000 |
| warn | WARN | #F2A33A | #FFB04A | #FA0000 |
| err | ERR | #FF7B72 | #FF6A3D | #FE0000 |
| input | INPUT | #0E1116 | #150D08 | #0F0000 |
| band_spectrum | SPECTRUM_FILL | #2F6BFF (alpha 70/255) | #FF9A2F at 0.40 | #F80000 at 0.38 |
| band_sky | SKY_FILL | #B9822E (alpha 55/255) | #9E5A22 at 0.14 | #900000 at 0.12 |

Further tokens with no name in the list above: DIM (disabled text), AMBER_HOVER, AMBER_DIM and AMBER_DIM_TEXT
(primary button hover / disabled), TEAL (target curves), BLUE (info, radio buttons), GREEN / RED (success / error,
the same values as ok / err), CARD_ERROR, CURRENT_BG (Library selection), MISSING_BG (banner), SPRING / VIOLET /
SKYBLUE / PINK (Response and telluric curves), SAT (saturated columns), SPECTRUM_TEXT / SKY_TEXT (band outlines),
ICON (the copy icon) and WHITE (confetti).

**Default values:** the spec of 2026-11-19 listed reference values for default (bg #0B0E13, accent #EF9F27, …) that
differ slightly from the app; as asked, the existing values were kept, so the default look is unchanged.

## Rules for night and red

- No light backgrounds anywhere; no green or blue: every token has G ≤ R and B ≤ R (red: G = B = 0).
- States are told apart by brightness and by shape or glyph, never by hue alone. Rail: current = solid accent block
  with dark text, done = mid block with ✓, not run = dim block, missing data = outline plus "!" in front of the
  label. Check lines keep ✓, "!" and ✗. Error lines are the brightest.
- Sky bands have a dashed outline, the spectrum band a solid one. Curves that differ only by colour in default also
  differ by line style or width: Response observed solid, library dashed, raw dotted, smoothed solid and thick;
  corrected target solid, reference dashed; Acquisition sky dotted.
- 2D images (subs, stacks, Select previews, Geometry / Extract viewers, the frame strip) use a monochrome map from
  black to the accent (night amber, red red); default keeps the grey map. The map follows the palette live.
- The logo is monochrome (prism and beam in text, the fan in tints of the accent fading to the background); the About
  splash photo goes through the same black → accent map. Confetti uses accent tints.

## The through-filter rule

A red filter over the screen passes only the red channel. `tools/check_palette.py` prints, for every palette, the
WCAG contrast of the important pairs both normally and through the filter (G and B set to 0, then the usual relative
luminance). Thresholds: 4.5:1 for text; 3:1 for outlines and large elements; the rail state fills (current, done,
not run) at least 1.5:1 apart. Night and red must pass through the filter (tests/test_themes.py); default is
reported only.

**Rail labels count as large elements (3:1, not 4.5:1).** Through a red filter the whole range is 0.05 … 0.26 in
relative luminance (5.25:1 at most). Three state fills at least 1.5:1 apart with 4.5:1 text on each cannot exist in
that range (the middle fill would need its text both ≥ 4.5× brighter and ≥ 4.5× darker than itself). The rail labels
are therefore held to the large-element threshold, and the states are also shown by glyph (✓, "!") and by outline.
Text on bg / panel, muted text, ok / warn / err, and accent text meet 4.5:1 through the filter in night and red. In
the red palette muted text is nearly as bright as text: that is what 4.5:1 through the filter requires.

## Adding a palette

1. Add a dict to `theme.PALETTES` with every key of `default` (tests/test_themes.py checks the set).
2. If two tokens share a value in the new palette, they must share it in default too, or a live switch back cannot
   tell them apart (`colour_map`; test_baked_colours_round_trip_through_night does this for night).
3. Run `python tools/check_palette.py` and `pytest tests/test_themes.py`.
4. Add the name to `PALETTE_NAMES` and to the Settings dropdown when it exists.

## How live switching works

`retheme.switch(name)`: rebind the attributes (`theme.set_palette`), re-apply the style sheet, QPalette and
pyqtgraph defaults, then translate every colour baked into a widget's own style sheet or a plot item (pens, brushes,
legends, axes, text items, backgrounds) through `colour_map(old, new)`, keeping alpha. Widgets with `retheme()`
(image viewers, previews, the Plan's icons) or `recolour(mp)` (legend swatches) redo their part; the band overlays and
the gear listen to `theme.notifier.changed`. Custom-painted widgets (rail, switch, logo, frame strip) read the
theme when they paint. Toggling twice gives the exact default pixels again (tested on every page).

Dialogs: `AA_DontUseNativeDialogs` is set in `theme.apply`, so file dialogs are drawn by Qt in the palette (native
Windows dialogs are white and ignore it); message boxes, input dialogs, tooltips, menus, combo pop-ups, scroll bars,
selections and the log panel are styled by the app style sheet.

## Windows title bar

In night and red the app asks Windows for the dark title bar (`DwmSetWindowAttribute`, attribute 20, 19 on older
Windows 10; best effort, failures ignored). Windows only offers light or dark: **the title bar cannot be made red
or amber**; in night and red it is dark grey. It is the one part of the window the app does not draw.

## Dialogs (2026-11-21)

**Root cause of the black file dialog.** Three things together: (1) the global rule `QPushButton, QToolButton {
padding: 8px 14px; border: 1px; min-height: 22px }` also hit the file dialog's 33 px toolbar buttons, leaving 2-3 px
for a 16 px icon (the "tiny dots"); (2) what was left of the icons were Fusion's standard glyphs, drawn dark for a
light theme; (3) the QPalette (Window and Base) and `QFileDialog { background: bg }` gave the dialog, sidebar, list,
header and fields one near-black colour. The title bar was white because only the main window asked DWM for the dark
one.

**The shared helper** is `ui/dialogs.py` with the rules in `theme.dialog_css()`; every dialog inherits them:

- **Surfaces.** Dialog background `panel`; list/tree views `bg` with an alternating row (`bg` mixed 3.5 % towards
  `text`), hover `bg`→`accent` 16 %, selection 34 % (channel by channel, so the red palette stays red-only); sidebar
  and header `panel2`; line edits, combo boxes and their popups `input` / `border_strong` with the accent border on
  focus (the app's field style); buttons as everywhere; labels `text`, the informative text of a message box
  `text_muted`; disabled items and labels `not_run_text`.
- **Toolbar icons** (back, forward, up one level, new folder, list view, detail view): our own line icons, SVG drawn
  in `text` (disabled: `dim`) at 16 and 20 px, crisp at device pixel ratio 1 and 2. Found by object name
  (`backButton`, `forwardButton`, `toParentButton`, `newFolderButton`, `listModeButton`, `detailModeButton`, the
  names in the installed Qt 6), by type and order if a Qt version names them differently. Small square buttons with
  a hover (`raised`), pressed and down (the active view mode; Qt marks it with setDown) state.
- **Night palettes.** Folder, file, drive and computer icons from SVG in the palette (the system ones are blue and
  yellow), also in "Look in" before the folder is listed; a palette glyph (? ! i ×) in message boxes; no size grip
  (Fusion draws it grey).
- **Directory mode.** Files of the folder are listed greyed and cannot be selected (a proxy model clears
  Enabled/Selectable on files; `ShowDirsOnly` off), with the name filter `*.fit *.fits *.fts` shown as "FITS files
  and folders"; only folders can be chosen.
- **Title bar.** The dark title bar (the same best-effort DWM call as the main window) for every dialog in every
  palette.
- **Every dialog.** The file and folder pickers go through `dialogs.existing_directory / open_file / open_files /
  save_file` (no static `QFileDialog.get…` calls are left; a test checks). Dialogs opened elsewhere (static
  QMessageBox / QInputDialog calls, custom dialogs) are styled when they first take the focus
  (`QApplication.focusChanged`, not an app-wide event filter). Non-native everywhere (`AA_DontUseNativeDialogs` plus
  `DontUseNativeDialog` on each file dialog).
- **Hard-coded colours.** None in `ui/dialogs.py`; the About dialog keeps its `bg` background on purpose (the credit
  roll), from the token.
- **Known, app-wide:** Qt draws the text caret of every line edit inverted (`RasterOp_NotDestination`), so on the
  night `input` colour it is a bluish white, on red cyan. It is one pixel wide and blinks; the pixel tests leave it
  out. On real Windows, ClearType also gives text faint coloured fringes (sub-pixel anti-aliasing).

Tests: `tests/test_dialogs.py` (offscreen grabs in all three palettes: panel background, the surfaces differ, text
contrast ≥ 4.5:1, toolbar icons ≥ 3:1 with real icon pixels, selection, greyed FITS files, Open/Save, QMessageBox and
QInputDialog, no green or blue in night and red).

## Help me (2026-11-21)
The top row has a small **Help me** button (a ? icon in `text`, the pill style of the pair chip) and F1: a window
beside the app with the workflow guide (`starextractor/resources/help.md`), contents and find. Styled from tokens
(`QTextBrowser#helpText`, `QListWidget#helpContents`).
