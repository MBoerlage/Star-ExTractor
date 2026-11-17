# Sample data and what it tells us

Real frames live one folder up, in `..\projects\` (not part of the repository).

| Folder | Content |
|---|---|
| `projects\Lights\Science\` | `Sheliak_00015…00029.fits` (15 frames) |
| `projects\Lights\ReferenceStar\` | `HD174602_00001…00011.fits` (11 frames) |
| `projects\calibration\darks\` | 11 darks, 10 s, gain 251, offset 20 (NINA) |
| `projects\calibration\bias\` | 11 bias, 0.001 s, gain 251, offset 20 (NINA) |
| `projects\output\` | Alioth/Phecda frames renamed for SpecINTI; ignore |

## Header facts (verified from the files)
- Camera `ZWO ASI585MM Pro`, 3840 × 2160, BITPIX 16, **BZERO 32768**, pixel 2.9 µm, bin 1×1, ROWORDER TOP-DOWN.
- Lights: SharpCap 4.1, `IMAGETYP='Light'`, EXPTIME 15 s, **GAIN 250, OFFSET 3**, SET-TEMP −11, CCD-TEMP −7.7, OBJECT set, `DATE-OBS` UTC. No RA/Dec, no site keywords.
- Darks and bias: NINA 3.2, `IMAGETYP='DARK'` / `'BIAS'` (upper case), **GAIN 251, OFFSET 20**, SET-TEMP −10, with SITELAT/SITELONG of the observer's site, SITEELEV 0.0, TELESCOP 'TS optics', FOCALLEN 790.
- Frame-type keyword differs by software: normalise `Light/LIGHT`, `Dark/DARK`, `Bias/BIAS/Offset`, `Flat/FLAT`.

## Measured on `Sheliak_00015.fits` and `HD174602_00001.fits`
- Medians: light ≈ 1008 ADU, dark ≈ 2048, bias ≈ 2064. The dark/bias offset (20) is higher than the lights' (3), so a plain dark subtraction leaves the light about 1000 ADU too low. Sky-band subtraction in the 1D extraction removes a constant pedestal, so the final spectrum is not hurt, but the 2D view is. See SPEC §4 for how the tool handles this.
- Spectrum is horizontal, centred near row **1330** (Sheliak peak row 1331, reference 1345; FWHM about 6 px in Y for the science star).
- Tilt: spectrum centre moves from row 1336 at x=600 to row 1327 at x=3200, so about −9 px over 2600 px, **about −0.2°**. Detection must find this.
- Bright signal on Sheliak spans about x = 1100–2530; the reference star spans about x = 810–2420 (colder, fainter blue end).
- Hot pixels reach 65520 ADU (16-bit max); expect them in the dark master.
- The usable band for publishing is **3900–7700 Å** (decided; keeps the O₂ A telluric band). Dispersion is about **1.2 Å/px** (from Matt's BASS fit of `Gam Cas_00004.fits`: 348.4–811 nm over the 3840 px; see `BASS_CALIBRATION.md`). Do not use the 1.85 Å/px shown in the mockups. With that dispersion the bright region of Sheliak (x about 1100–2530) is roughly 4860–6560 Å.

## What is missing from the sample set
- No neon/lamp frame, no flats, no Hα-emission science target. Tests for those use synthetic frames.
- No known-good reduced spectrum to compare against. Matt can reduce the same data in SpecINTI/BASS and supply a comparison spectrum later (OPEN item in BUILD_PLAN).

## Reference (library) spectra: `starex_reduce\reference_spectra\`
- Supplied by Matt: Pickles (1998, PASP 110, 863) templates from the HILIB/UVILIB set plus `README.HILIB`, with the metal-weak/metal-rich `w…` and `r…` variants removed (the folder now holds only the standard `<type><class>.dat` files). Same set ISIS/BASS users know.
- Format: plain text, two columns `wavelength(Å)  flux`, 3000–10000 Å in 5 Å steps (1401 lines), flux normalised to 1.0 at 5556 Å. Resolution about **10 Å**, coarser than our data (see SPEC §5.5).
- File names: `<type><class>.dat`, e.g. `a0v`, `a0iii`, `b8v`, `g2v`, `k5iii`, `m2.5v`. Parse whatever `*.dat` files are present; never assume a particular file exists (fall back to the nearest type).
- The reference star in the sample set (HD174602) and Vega-type A0V stars map to `a0v.dat`. Coverage includes the whole publishing band 3900–7700 Å.
- Wavelength regions contaminated by atmospheric absorption were generally zeroed or interpolated in the library, per the README. Treat exact zeros as invalid and interpolate over them.
- Cite Pickles 1998 in the app's About box.

## Worked example
A complete real-world reduction (raw, BASS stacked, 1D, response, submitted AAVSO file) is in example_spectra/novasrg2026. See docs/EXAMPLE_NOVA.md. Use it for acceptance tests; never modify it.

Calibration frames for the nova example (bias/ and darks/ in example_spectra/novasrg2026) are gain 251/offset 20, lights gain 200/offset 3: see EXAMPLE_NOVA.md 'Calibration mismatch'. Earlier notes saying lights were gain 250 are superseded.
