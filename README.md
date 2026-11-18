# Star ExTractor

Star ExTractor turns the frames from a **Star'Ex LR**, **TSE**,**Alphy600** slit spectrographs into a calibrated, response-corrected 1D
spectrum, ready to upload to the **AAVSO Spectroscopy Database (AVSpec)**. It plans the night, watches your subs
while you capture, reduces them step by step and writes the AVSpec file. It is a Windows desktop program; you do
not need Python.

![The Response step: the instrument response from the reference star](screenshots/latest/13_response.png)

## Download

Get the latest installer from the **Releases** page: `https://github.com/MBoerlage/Star-ExTractor/releases`
(`StarExtractor-Setup-<version>.exe`). A portable zip (`StarExtractor-<version>-portable.zip`) is there too, for
running without installing. `SHA256SUMS.txt` lists the checksums of both.

## Install

1. Run `StarExtractor-Setup-<version>.exe`. It installs for your user account only, so no administrator rights are
   needed (you can choose "install for all users" on the first page if you are an administrator).
2. Windows may show **"Windows protected your PC"** (Microsoft Defender SmartScreen), because the program is not
   code-signed. Click **More info**, check that the app is *StarExtractor-Setup-…exe*, then click **Run anyway**.
3. Pick a folder, optionally tick *Create a desktop shortcut*, and finish. Start it from the Start Menu
   (*Star ExTractor*).

Portable zip: unzip anywhere and run `StarExtractor.exe` (SmartScreen may ask the same way).

Uninstall from *Settings → Apps*. The uninstaller asks whether to remove your settings and master library as well;
your session folders and exported files are never touched.

## Quick start

1. **Plan**: type the target (or check it with SIMBAD), pick a reference star from the list of nearby stars and
   copy the observing plan.
2. **Acquisition**: point it at the folder your capture program saves to, press *Start watching* and collect the
   target and reference subs. Each sub is checked for saturation and signal to noise while you image.
3. **Reduce**: in *Files*, use the suggestion from Acquisition (or pick the lights), add darks and bias to the
   master library and stack. Then run *Geometry*, *1D Extract*, *Wavelength* and *Response* (or *Run all*).
4. **Export → Collect data**: check the object name and fill in your AAVSO observer code, site and equipment
   (once; they are remembered).
5. **Export → Test**, then **Publish**: save the AVSpec file (and, if you like, the 1D FITS, CSV and PNG).
6. **Upload the AVSpec file to AAVSO yourself** on the AVSpec website. Star ExTractor never uploads anything.

**No telescope at hand?** Create a small synthetic example night and walk through all the steps with it: press
*Win+R* and run

```
"%LOCALAPPDATA%\Programs\Star ExTractor\StarExtractor.exe" --make-example "%USERPROFILE%\Documents\StarExtractor example"
```

(use your install folder if you chose another one). It writes `target`, `reference`, `darks` and `bias` folders.

## Supported hardware

One instrument, on purpose: the **Star'Ex LR** slit spectrograph with a **ZWO ASI585MM Pro** camera. Frames from
SharpCap and N.I.N.A. (FITS, 16 bit) are read directly.

## Where your data lives

* Your **session folders** (wherever you create them): the reduced files and `export\` with the files you publish.
* `%APPDATA%\starex_reduce\`: settings, the instrument profile, the master library, saved responses, the VSX cache
  and `error.log`. Nothing is written in the program folder.
* The observer code, site and equipment stay in your settings; they are never sent anywhere except into the files
  you export.

## Troubleshooting

* **SmartScreen blocks the installer**: see *Install*, step 2.
* **Something went wrong**: the error window has *Open log folder*; `error.log` there has the details. Please attach
  it to a bug report.
* **Check the installation**: run `StarExtractor.exe --selftest` from a command prompt in the install folder. It
  reduces the small example without opening a window and prints `self-test PASSED` (exit code 0) when all is well.
* **Start fresh**: close the app and rename `%APPDATA%\starex_reduce`; it is recreated with defaults.
* **The taskbar shows an old icon** after an update: Windows caches icons; it refreshes after signing out.
* **Network**: only on a click (SIMBAD/Sesame name lookup, one VSX lookup in Export). Everything else works offline.

## For developers

```
pip install -e ".[dev]"
ruff check .
pytest -q
python -m starex_reduce.app            # run from source
```

Build the installer (needs `pip install -e ".[dev,build]"` and Inno Setup 6):
`powershell -ExecutionPolicy Bypass -File packaging\build.ps1`. See `docs/PACKAGING.md` and `CONTRIBUTING.md`.
The full real example night (V8055 Sgr) used by the `realdata` tests is not in the repository; see
`docs/EXAMPLE_NOVA.md`.

## Licence

MIT, see `LICENSE`. Third-party data and software: `THIRD_PARTY_NOTICES.md`.

## Credits

<!-- credits:start -->
**STAR EXTRACTOR**  
Spectroscopy reduction for slit low res spectrographs

### Credits

**Stellar flux library**  
Pickles, A. J. (1998). “A Stellar Spectral Flux Library: 1150-25000 Å.”  
Publications of the Astronomical Society of the Pacific, 110, 863-878.  

**Reference star database**  
Francois Teyssier and Paolo Berardi  

**Spectroscopy submissions**  
AAVSO Spectroscopy Database (AVSpec) and Variable Star Index (VSX)  

**Target resolution**  
This application has made use of the SIMBAD database,  
operated at CDS, Strasbourg, France.  

**Built with**  
Python, PySide6 (Qt), NumPy, SciPy, Astropy, pyqtgraph  

Thank you to everyone who shares their data, tools and knowledge  
with the amateur spectroscopy community – to the stars and beyond!
<!-- credits:end -->
