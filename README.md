# chemfigstudio

**Condensed-formula chemistry figures, generated locally.** No network, no browser
engine, no third-party drawing app — one small Python package and Pillow.

![chemfigstudio GUI](docs/images/gui-light.png)

*Local web GUI (1440×900). Dark mode ships too — see `docs/images/gui-dark.png`.*

**Repository:** https://github.com/Tracecatalyst/chemfigstudio


---

## What it draws

Seven figure types, all reproducible from a command or one API call:

| Figure | What it shows | Preview |
|---|---|---|
| `alcohol` | one straight-chain alcohol: condensed structure, name, formula, literature ΔHc | ![alcohol](docs/images/figure-alcohol.png) |
| `series` | many alcohols row by row, for "longer chain → more heat per gram" reasoning | ![series](docs/images/figure-series.png) |
| `reaction` | complete combustion, **auto-balanced** from the carbon count, with ΔH | ![reaction](docs/images/figure-reaction.png) |
| `chcount` | every corner labelled with its C–H count (3 C–H / 2 C–H …) | ![chcount](docs/images/figure-chcount.png) |
| `enthalpy` | class measurements vs literature, bar chart with % deviation | ![enthalpy](docs/images/figure-enthalpy.png) |
| `esterification` | carboxylic acid + alcohol → ester + water | ![esterification](docs/images/figure-esterification.png) |
| `skeleton` | an editable atom/bond graph (rings, branches, N/S/Cl/Br/P) | ![skeleton](docs/images/figure-skeleton.png) |

> Auditing this repository? Start at [`docs/AUDIT.md`](docs/AUDIT.md) — it lists what has
> already been checked (so you do not re-report it), the invariants that must hold, and the
> blind spots worth attacking.

## Why this exists

- **Fully local.** Nothing leaves the machine; the tool works offline in an exam room.
- **Deterministic.** Same input → byte-identical PNG. The test suite pins hashes, so
  refactors cannot silently change a figure you already published.
- **Vector when you want it.** `--format svg` emits a real SVG whose labels are real
  `<text>` (searchable, selectable, scalable), not outlined paths.
- **No chemistry toolkit required** for straight-chain alcohols. If RDKit is present,
  it is used automatically for branched/ring/exotic SMILES.
- **Bilingual UI and messages** (English / 简体中文) with a key-parity test.

## Install

Two ways in — pick based on whether you want to **use it** or **change it**.

### Use it (no Python needed on macOS)

Download `ChemFigStudio.app` from the [latest release](../../releases/latest), unzip, and
double-click. It bundles Python, Pillow and RDKit, so nothing else is required. First launch
on someone else's Mac needs right-click → Open (or
`xattr -dr com.apple.quarantine ChemFigStudio.app`), because the build is only ad-hoc signed.

### Windows

There is **no `.exe`** — the packaged app is a Swift/WKWebView shell, so it is macOS-only.
On Windows the tool still runs fine (the UI is the same web page), it just needs Python:

1. Install [Python 3.9+](https://www.python.org/downloads/) (tick "Add Python to PATH")
2. `git clone https://github.com/Tracecatalyst/chemfigstudio.git`
3. Double-click `scripts\launch-gui.bat`

That script finds Python, installs Pillow **into your user directory** if it is missing,
creates an output folder on the Desktop, and opens the browser. No admin rights, nothing
system-wide.

### Any other platform

Requires Python 3.9+ and Pillow. Nothing is installed system-wide:

```bash
git clone https://github.com/Tracecatalyst/chemfigstudio.git && cd chemfigstudio
PIP_USER=1 python3 -m pip install --user pillow      # if you do not have Pillow yet
make test                                            # 475 tests, hermetic (no network)
```

RDKit is optional: without it you still get all seven figure types, skeletal editing and
molblock/SDF in and out; with it you also get arbitrary SMILES and the 3D conformer
(`python3 -m pip install --target .pylibs rdkit numpy`).

Current version: **0.5.0** (`python3 -m chemfigstudio.cli --version`).

## Quick start

### 1. Web GUI

```bash
make gui            # starts http://127.0.0.1:8765 and opens it
```

The GUI has three columns: **parameters** on the left, the **canvas** in the middle,
**layers / output** on the right. Typing in a parameter re-renders the preview
immediately; `Generate` writes the file; `Show in Finder` reveals it.

Each document is a **tab**. `New tab` opens another figure, clicking a tab switches
documents (parameters and preview follow), `Close tab` drops it, and double-clicking a
tab renames it. Undo/redo steps through a 100-deep history per tab.

### 2. Command line

```bash
cd chemfigstudio                             # or wherever you cloned it
PYTHONPATH=src python3 -m chemfigstudio.cli alcohol --n 4 --name butan-1-ol --dh -36.1
PYTHONPATH=src python3 -m chemfigstudio.cli series --include-methanol -o series.png
PYTHONPATH=src python3 -m chemfigstudio.cli reaction --n 4 --dh-mol -2670 --dpi 300
PYTHONPATH=src python3 -m chemfigstudio.cli doctor
```

Every subcommand accepts `--format png,svg`, `--dpi`, `--transparent`, `--json` (machine
readable result) and writes into the output directory shown by the GUI footer.

### 3. Python API

```python
import sys; sys.path.insert(0, "src")
from chemfigstudio import api

result = api.render("reaction", out="combustion.png",
                    params={"n_carbons": 4, "delta_h_kj_per_mol": -2670},
                    formats=("png", "svg"), dpi=300)
print(result.size, result.warnings)
```

## Editing a skeleton

`Edit skeleton…` opens the atom/bond editor: click to add a carbon, click a bond to
cycle single → double → triple, pick an element from the palette, drop functional
groups, then `Apply` to push the graph through the same renderer. `skeleton` accepts the
graph as JSON, so an editor session is reproducible in a script:

```bash
PYTHONPATH=src python3 -m chemfigstudio.cli skeleton \
  --skeleton '{"atoms":[{"id":"a","element":"C","x":100,"y":100}],"bonds":[]}'
```

## Batch

`Batch…` takes a CSV (`kind,param,param,…`) and renders every row, reporting per-row
errors instead of stopping. Useful for a class set of alcohols:

```csv
kind,smiles,name,delta_h
alcohol,CO,methanol,-22.7
alcohol,CCO,ethanol,-29.7
```

## Export

`Export image…` writes PNG / SVG / PDF, optionally transparent, at a chosen DPI, and
refuses to overwrite an existing file unless you confirm.

## Structure files in and out

The `skeleton` field accepts **three kinds of input** and sniffs which one it got:

| input | example | dependency |
|---|---|---|
| **SMILES** | `c1ccccc1O` | RDKit (2D layout + stereochemistry) |
| **molblock / SDF** (V2000) | a `.mol` from ChemDraw / PubChem | **none** — parsed as plain text |
| skeleton JSON | the editor's own format | none |

What you get back is an ordinary editable skeleton: drag atoms, change bond orders, or render
it. Wedge bonds are supported; the **narrow end is always the stereocentre** (drawing it the
other way round is the other enantiomer).

Going the other direction, `chemfigstudio convert` writes structure files:

```bash
chemfigstudio convert --skeleton "CC(=O)Oc1ccccc1C(=O)O" -o aspirin.mol    # molblock (V2000)
chemfigstudio convert --skeleton "CCO" -o ethanol.smi                      # canonical SMILES
chemfigstudio convert --skeleton "CCO" -o ethanol.inchi                    # InChI
chemfigstudio convert --in aspirin.mol -o back.inchi                       # round-trip
```

The format is inferred from the output extension. **molblock needs no RDKit** — that path is
plain text, so it works even without the optional dependency. SMILES and InChI do need it
(canonicalisation and identifiers are algorithm problems, not drawing problems).

Two honest limits: exported molblocks contain **no explicit hydrogens** (hydrogens are
implicit in a structural formula, so there are no hydrogen coordinates either — the output is
not a 3D structure), and only **V2000** is written.

### Valence checking

Imports and renders report atoms whose bonds exceed the element's valence. The rules are
deliberately conservative so it does not cry wolf: valence is the **sum of bond orders**,
**under-valence is never reported** (hydrogens are implicit), formal charge adjusts the limit
(`N⁺` with four bonds is ammonium), and elements outside the table are skipped entirely.

## 2D vs 3D comparison

`/compare` puts the condensed formula and the real 3D conformer of the same molecule side by
side, several molecules at once, and lets you drag the 3D view. Each row can be exported as a
single PNG containing both halves.

Like the 3D viewer it is a separate page loaded on demand, and the 3D half is a WebGL canvas
— which is why the export composes the image in the browser rather than on the server (the
server has no pixels for it).

## 3D viewer

A second page (`/viewer3d`, also reachable from the **3D molecule viewer** button in the
toolbar) builds a **real 3D conformer** from a SMILES and lets you rotate it.

```bash
python3 -m pip install --target .pylibs rdkit numpy   # optional dependency
chemfigstudio render3d --smiles CCCCO -o butanol.sdf  # conformer as molblock/SDF
```

RDKit is discovered automatically from `<project>/.pylibs` or `<project>/vendor`; if you keep
it elsewhere, point `CHEMFIG_PYLIBS` at it (or raise the upward search with
`CHEMFIG_PYLIBS_DEPTH`, default 4).

- The conformer is generated by **RDKit** (ETKDGv3 multi-conformer search, then MMFF94;
  the lowest-energy conformer wins). A fixed random seed means the same molecule always
  yields the same conformer, so figures are reproducible.
- Rendering is done by **3Dmol.js**, vendored into `gui/static/vendor/` — no CDN, so the
  viewer works offline.
- Representations: ball-and-stick, space-filling, sticks, wireframe, plus element labels,
  auto-rotate, fit-to-view and reset view.
- **Export PNG** redraws the scene on a separate large canvas (up to 3000 px ≈ 300 dpi,
  optional transparent background) and downloads it.
- RDKit is optional: without it the viewer says so and tells you how to install it. The
  macOS bundle can embed it (`scripts/build-macos-app.sh`, ~113 MB app).

Note that 3D still frames are **raster only** — there is no SVG output for the 3D view.

## Plugins

Drop a Python module into your plugins directory and it can register extra figure
kinds (`chemfigstudio_plugin_entry`). `CHEMFIG_PLUGINS=off` disables loading; bundled
examples live in `plugins/`.

## macOS app (optional)

```bash
make app                 # dist/ChemFigStudio.app   (current arch)
make bundle-universal    # Intel + Apple Silicon
```

The app is a thin Swift/WKWebView shell around the same local server. It is unsigned
(no developer certificate), so the first launch needs the usual Gatekeeper detour.

## Development

```bash
make lint        # syntax check (ast.parse)
make typecheck   # mypy
make test        # 468 unit tests, hermetic
```

Every fix lands with a test in the same commit, and rendering refactors are proven by
byte-comparison against the previous output.

## Limitations

- The built-in SMILES parser only understands straight-chain primary alcohols; install
  RDKit for anything else.
- 3D needs RDKit (optional). 3D still frames are raster only (no SVG), and the 3D view is not part of
  `make figures` or the CSV batch path.
- `series` rows are literature-fixed unless you pass `--include-methanol` or `--entries`.
- The macOS app is unsigned.

## License

MIT — see [LICENSE](LICENSE).
