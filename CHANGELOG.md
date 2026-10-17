# 更新日志（CHANGELOG）

本文件格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **`docs/ROADMAP.md`** turns "what is still missing" into a file (it previously lived only
  in a conversation). It is graded gold / silver / bronze, every completed item carries
  evidence (commit hash or code location), and there is an explicit "deliberately not doing"
  list (reaction balancing, spectra, docking, 3D editing, a second UI).
- **`tests/test_repo_health.py`** — four repository guards: no references to unallocated
  ADRs, no duplicate class names in a module, no duplicate method names in a class, and
  ROADMAP completed items must carry evidence.

### Fixed

- **A test was silently swallowed.** `tests/test_export.py` had two classes named
  `FormatDocumentationTests`; the second replaced the first, so two tests never ran
  (`unittest` reports nothing and still exits 0). Removing the duplicate immediately exposed
  a failure: one test hard-coded the version heading `## [0.4.0]`, which cannot match here.
  It now finds the first released version with a regex.
- **A comment described something that does not exist**: `skeleton.py` pointed at
  `Skeleton.valence_errors` (never implemented) and an unallocated ADR. It now states plainly
  that `VALENCE` is data only, with no checking logic yet.


### Changed

- **The comparison page now shows several molecules side by side.** Six presets render at
  once, one row each (2D formula left, rotatable 3D conformer right), with "show only this
  one" for any SMILES and "restore all examples"; the toolbar also gained auto-rotate and
  element-label toggles.

  Two problems had to be solved for that, not hand-waved:
  * **2D requests must be serialised** — `api.preview()` writes the fixed filename
    `preview.png`, so six concurrent requests overwrite each other and every row ends up
    showing the same molecule. The page now funnels them through a promise chain (`twoDChain`)
    while 3D stays concurrent (it returns JSON). Verified: the six rendered formulas have
    different pixel dimensions, which they could not if one file were shared.
  * **3D viewers initialise lazily** (on scroll), so six WebGL contexts do not hit the
    browser limit at once; stale async results are dropped by a render token.


### Added

- **The 2D vs 3D comparison is now part of the app.** A new toolbar button opens `/compare`:
  textbook condensed formula on the left, the same molecule's real 3D conformer on the right
  (drag to rotate), plus an example dropdown (cyclohexane, benzene, alanine, aspirin,
  caffeine, nicotine) with a note on what 2D cannot show.

  **No new API was added** — 2D reuses `POST /api/preview` (nothing written to the output
  directory) and 3D reuses `POST /api/render3d`, so the server gained only a page route and
  zero duplicated rendering logic. The page also guards against double-clicks: `preview.png`
  has a fixed filename, so a stale response is dropped rather than drawn.


### Fixed

- **RDKit was installed but reported as missing.** The dependency search window went up
  four levels, which reaches the project root but not a `vendor`/`.pylibs` kept above it, so
  the viewer said "RDKit required" on a machine that had RDKit. The default is now eight
  levels (only directories that actually contain `rdkit` are used), and
  `CHEMFIG_PYLIBS_DEPTH` still overrides it.
- **That change exposed a real leak**: `smiles._resolve_rdkit` did not wrap RDKit in
  `quiet_rdkit()`, so `SMILES Parse Error` went straight to stderr, breaking the
  "library code never prints" rule. The helper now lives in one place (`smiles.py`) and all
  three entry points use it. The regression test had to change too: RDKit logs to *file
  descriptor 2*, which `contextlib.redirect_stderr` cannot capture — the first version passed
  vacuously. It now runs a subprocess and asserts on its stderr.


### Fixed

- **`dist/` accumulated several identically-named app bundles.** `make bundle` and
  `make bundle-universal` produce different artifact names, and the universal build used to
  leave its two architecture slices in `dist/universal-arm64/` and `dist/universal-x86_64/`
  forever — so Finder/Dock/Launchpad showed three or four copies with the same icon and no
  way to tell which one mattered. The slices now go to a temporary directory and are removed
  on exit (also on failure), and `make apps` lists what is in `dist/` and says which bundle
  is the one to hand to someone else.
- A BSD `mktemp` gotcha this surfaced: the template does not create parent directories, and
  `$TMPDIR` often ends in `/`, so the arm64 slice succeeded while the x86_64 one failed.


## [0.4.3] - 2026-10-16

### Added

- **Structure import — the tool can finally read real molecules.** The `skeleton` field
  (CLI and GUI) now accepts three kinds of input and sniffs which one it got:
  1. **SMILES** (e.g. `c1ccccc1O`) — RDKit supplies the 2D layout and stereochemistry;
  2. **molblock / SDF** (V2000) — parsed as plain text, **no dependency at all**;
  3. the original skeleton JSON.
  What you get back is an ordinary editable skeleton: drag atoms, connect bonds, change
  bond order, or render it as a figure. Verified on aspirin (13 heavy atoms), benzene and
  L-alanine.
- **Minimal stereochemistry**: `Bond.wedge` (`none|up|down`) plus solid/hashed wedge
  rendering. The **narrow end is always the stereocentre** (`known_stereo` from RDKit wins;
  a plain molblock falls back to a degree heuristic, and equal degrees mean *no guessing*) —
  drawing it the other way round is the other enantiomer, so this convention is pinned by
  tests.
- `Atom.charge` (formal charges, needed for N+/O- molecules) and a wider element table
  (8 → 11: added F, I, B).
- `Skeleton.parse_skeleton_input()` — one entry point for all three input kinds.

### Fixed

- **`Atom.text()` no longer falls back to `ELEMENT_LABELS`.** That table means "default
  group label when switching an element" (`C` → `CH_{3}`), not a render default — the
  fallback turned every skeletal carbon into a methyl and made imported molecules look like
  piles of `CH_{3}`.
- **Heteroatom labels were looked up per element instead of inferred from bonding.** Every
  nitrogen came out as `NH_{2}` — so caffeine's three nitrogens (two N-methyl, one ring `=N-`)
  were all drawn with two hydrogens. Hydrogen count is now `valence − Σ(bond order)` adjusted
  for charge, which yields `OH`/`NH`/`NH_{2}`/bare symbols correctly (pyrrole → `NH`,
  pyridine → `N`, caffeine → four bare `N`).
- **A carboxylic acid's double-bonded oxygen was labelled `OH`** instead of `O`; labels now
  respect bond order.
- **Multi-molecule scene JSON was downgraded to a single molecule** by the new unified input
  path (caught by an existing test; now covered by a regression test).


## [0.4.2] - 2026-10-16

### Changed

- **`core.py` no longer knows any chemistry.** `draw_chain` used to hard-code the
  `CH_{3}` / `H_{2}` / `OH` / `2 C-H` labels *and* the assumption that the chain ends in a
  hydroxyl, so the "no chemistry in core" rule was only true in the docs. Labels are now
  injected by the caller:
  - `core.ChainStyle` gained `head_label` / `vertex_label` / `end_label` / `end_colour` /
    `end_lone_pairs` / `note_label` / `note_head_label`; a bare `ChainStyle()` now draws an
    unlabelled zig-zag instead of an alcohol.
  - The condensed-formula vocabulary lives in `figures._ALCOHOL_VOCAB` +
    `figures.chain_style()`, which also owns the `label_style` (h2/ch2) meaning.
  - The lone-pair *interpretation* moved to `figures.draw_lone_pairs()`; core keeps the
    neutral primitive `core.draw_dot_pairs()` (old name kept as an alias).
  - **Byte-identical output**: all six example figures hash exactly as before.
  - New guard: `tests/test_core.CoreKnowsNoChemistryTests` walks core's AST for non-docstring
    strings and identifiers (word-boundary matching, so `"OH"` cannot match `draw_rich`) and
    asserts the vocabulary really is in `figures`, so the guard cannot pass vacuously.

### Fixed

- **ADR-004 claimed a single raster format while the code shipped SVG and PDF** (audit contract-01). Added
  **ADR-014** to supersede it, marked ADR-004 as replaced, and corrected
  `ARCHITECTURE.md §1/§7.4/§11` plus the README. New guard:
  `tests/test_export.FormatDocumentationTests` fails if current docs claim a single format
  (ADR text and historical changelog sections are exempt, with a reverse assertion that the
  exemption really stops at the current release).
- **Other stale `ARCHITECTURE.md` facts**: the layer diagram and module table said "four
  figure types" (there are seven), the module list omitted `layout`/`canvas`/`render3d`,
  §5's `ChainStyle` field list was out of date, and §10 listed 5 of the 16 test modules.


## [0.4.1] - 2026-10-16

### Security

- **The GUI wrote to any directory the request asked for.** `/api/export` and `/api/batch`
  passed the request's `dir` straight to the writer, so any web page the user had open could
  make the local server write a file anywhere on disk (reproduced: a POST produced
  `/tmp/.../ELSEWHERE/pwned.png`). Writing is now confined to the output directory plus an
  explicit `allowedWriteDirs` list you edit yourself in Settings (new textarea). Out-of-range
  requests get **403** with an actionable message.
- **Mutating endpoints accepted cross-site requests.** No Origin/Host check and no
  Content-Type check on the JSON body, so a cross-site "simple request" (text/plain or a form
  post, which skips CORS preflight) could change settings, export files or read a local CSV.
  Every POST now requires a loopback same-origin `Origin` when present, a loopback `Host`
  (blocks DNS rebinding) and `application/json`. Non-browser clients (curl, scripts) send no
  `Origin` and keep working.

### Fixed

- **Batch overwrite confirmation checked different filenames than it wrote.**
  `target_paths()` fell back to `"figure"` while `run_batch()` fell back to `kind`
  (`alcohol` by default), so the dialog inspected `01_figure.png` and the run overwrote
  `01_alcohol.png` — silent data loss. Both now share one default and one fallback.
- **Export file name showed a doubled extension** (`x.png.png`) when the typed name already
  had one. The file on disk was correct; the displayed path was not.
- **RDKit auto-discovery silently gave up in deeply nested checkouts.** The upward search
  window was a fixed 4 levels, which reaches the project root but not a `.pylibs` kept one
  level above it. It is now configurable: `CHEMFIG_PYLIBS_DEPTH` (default 4, capped at 12).

### Docs

- Corrected documentation drift: test counts, `make help`'s "4 example figures" (it writes
  6), the port (`8756` → `8765`), a `chemfigstudio list` example that does not exist, a
  `tools/chemfigstudio` path that does not exist in this repository, and `make lint` being
  described as ruff when it runs `ast.parse`.


## [0.4.0] - 2026-10-16

### Added

- **3D molecule viewer** — SMILES → a real 3D conformer → rotate it in the app, export a
  high-resolution still frame.
  - `render3d.py` wraps RDKit (ETKDGv3 multi-conformer + MMFF94, lowest energy wins, fixed
    seed so the same molecule always gives the same conformer) and returns V2000 molblocks.
    RDKit stays an **optional** dependency and its absence produces an actionable message.
  - `POST /api/render3d` + a separate `/viewer3d` page rendering with a **self-hosted**
    3Dmol.js (no CDN, works offline): ball-and-stick / space-filling / sticks / wireframe,
    element labels, auto-rotate, fit-to-view, reset view.
  - PNG export up to 3000 px (≈300 dpi) with an optional transparent background.
  - CLI: `chemfigstudio render3d --smiles CCCCO -o butanol.sdf` (`--json` for scripts);
    `doctor` now reports 3D capability. Exit codes follow the existing convention
    (3 = missing environment dependency, 2 = bad argument).
  - `scripts/build-macos-app.sh` can embed `rdkit` + `numpy` into `Resources/vendor`
    (~113 MB app); `--no-vendor` keeps the small build.

### Fixed

- **Static assets in subdirectories were always 403** — `_safe_child()` rejected any name
  containing `/`, so `static/vendor/` could not be served and the 3D page would have shown
  a blank canvas. It now accepts `allow_dirs=True` while still rejecting `..`, hidden files
  and escapes.
- **`smiles.rdkit_source()` reported "unavailable" for `.pylibs`/`vendor` installs** — it
  never called `ensure_rdkit_path()`, so a real RDKit was only found by accident when it sat
  in user-site. `rdkit_available()` is fixed the same way.
- **SMILES error text depended on whether RDKit was installed** — for `C`, the built-in path
  suggested "methanol is CO (a lone C is methane)" while the RDKit path only said
  "0 oxygens", so installing RDKit made the message *worse*. Both paths now share one
  pre-check.
- **RDKit wrote parse errors straight to stderr**, breaking the "library code never prints"
  rule; now wrapped in `rdBase.BlockLogs()`.


## [0.3.0] - 2026-10-16

### Added

- **文档更新**：README 刷新（七种图、编辑器能力表、四道闸门命令、数字与现状对齐）。

- **P3 布局层收尾：标题字体搬进布局** —— `BannerLayout` 增加 `title_bold` / `title_sub`，
  `alcohol`/`reaction`/`chcount`(31+21)、`enthalpy`(28)、`esterification`(26) 的字号全部来自
  `layout.BANNERS`；**6 张图仍逐字节零回归**（哈希与迁移前完全一致）。
- **阶段⑤ 收尾：原子元素切换** —— `skeleton.change_element()`（默认标签跟着换，
  **用户自定义标签不被覆盖**，未知元素/原子报错）；界面新增元素选择器（新原子用所选元素，
  选中原子后切换即改元素），暴露 `window.chemfigSetElement` 作为测试接缝。
- 测试 253 → **260**。


- **P3 布局层（第二切片）：其余 5 张一次性图也接入** —— `layout.BANNERS` 登记
  `alcohol`(1500×420) / `reaction`(2100×560) / `chcount`(1700×520) /
  `enthalpy`(1500×660) / `esterification`(1500×470) 的**画布尺寸与标题位置**，
  由 `figure_layout(kind)` 取用（未登记就报错，不会悄悄用默认值画出错尺寸的图）。
  **6 张图逐字节零回归**（150 dpi，含 `skeleton` 的动态尺寸图）：
  `e13e0f11…`/`3628117f…`/`6c832baa…`/`144d4fe3…`/`f4d2c34e…`/`27a31d09…`。
  新增 4 条测试：版式表数值、未知 kind 报错、`figures.py` 不得再出现这些画布尺寸字面量、
  每张图必须真的调用 `figure_layout`。
- 测试 249 → **253**。


- **阶段⑤ 收尾：多分子图层**（`skeleton.Scene`）：一张图里放多个骨架、**纵向堆叠**
  （`offsets()` 按各自包围盒让位，间距 `MOLECULE_GAP=60`），`add/remove/select`（**至少留一个**）；
  JSON 兼容两种写法 —— 旧的**裸骨架** `{atoms,bonds}` 继续可用，多分子用 `{molecules:[…]}`。
  渲染支持一次画多个分子；界面新增**分子列表**（新建 / 删除 / 点击切换当前分子），
  `state.skeleton` 始终指向当前分子，因此原有编辑逻辑一行未改。
- **拖动时实时吸附**：吸附开关打开时，拖动过程中就实时吸附（松手再吸一次，幂等）；
  顺带修 `mousemove` 的 CSS 缩放换算（与 mousedown 一致）。
- 测试 243 → **249**；浏览器回归 19 → **20 项**（多分子图层端到端）。


- **阶段⑤ 进阶（第一切片）**：骨架模型加 `snap_position()`（**键角吸 30°、键长吸 92 的倍数**，
  可指定参照原子或自动取最近原子，无参照则原样返回）、`hit_test_bond()`（**点—线段**距离，
  不是无限直线）、`set_bond_order()` / `cycle_bond_order()`（单→双→三→单）；
  渲染支持**三重键**（三条平行线）。界面：新增「切键级」模式（点键即切）与「吸附键角/键长」开关
  （松手时吸附），二者都经会话提交 → 仍可撤销/重做。
- 测试 233 → **243**；浏览器回归 18 → **19 项**。


- **P3 布局层（第一切片）**：新增 `layout.py` —— 把版式抽成**不可变数据**：
  `Slot`（列 x、垂直偏移、对齐、字号、颜色）/ `RowLayout`（画布宽、首行 y、行距、
  锯齿链步长、底部留白、各槽位）/ `BannerLayout`（一次性图的画布与标题）。
  `series_figure` 已接入：**列位/行距/字号全部来自布局**，函数只声明「哪个槽位画什么」，
  并支持 `layout=` 整套换版式。
  **重构前后逐字节一致**（150 dpi sha 5545e5ba…、300 dpi sha b14f1c8c…，尺寸相同）→ 视觉零回归。
  新增 `tests/test_layout.py`：几何数学（高度公式与旧硬编码一致）、槽位查错、
  `figures.py` 里**不得再出现** 760/1040/1800/168 等版式字面量、换布局后画面必须跟着变。
- 测试 223 → **233**。


- **P3 `--watch`（边改边重出图）**：`chemfigstudio batch molecules.csv --outdir out --watch`
  —— 第一遍立即出图，之后**只在 CSV 的 mtime 变化时**重出；`--interval` 设轮询间隔；
  CSV 写坏/缺列**不会退出**（记一条错误，等你改好再试）；Ctrl-C 干净停止。
  `batch.watch(..., iterations=N, sleep=fn)` 可注入迭代上限与 sleep，因此**可测**（4 条测试：
  首轮+改动各出一次、坏 CSV 不致命、文件缺失只报错、事件计数正确）。
- 文档：把早已完成的 **P2「SVG 后端」**与 **P2「英文界面」**从待办表划掉（附证据指针）。
- 测试 219 → **223**。


- **阶段⑤ 第一增量：可编辑骨架（数据层 + 渲染 + 画布交互）**：
  * `skeleton.py`：`Skeleton`/`Atom`/`Bond` 模型 —— 加/移/删原子（删原子**连带删键**，
    不留悬空键）、加/删键与键级、**命中测试**（拖拽选中用）、`bounds()`、JSON 往返、
    `Skeleton.chain(n)` 生成与锯齿链一致的默认直链。
  * `figures.skeleton_figure`：把骨架画出来（原子圆点 + 富文本标签、双键画两条平行线），
    颜色按元素（C/H/O/N…），PNG 与 SVG 同一份代码。
  * 新增第 **7** 种内置图类型 `skeleton`（参数 `skeleton`(JSON) + `title`），
    于是**预览/导出/批量/CLI/插件顺序**全部自动可用（CLI 子命令也自动生成）。
  * 界面新增**骨架编辑器**：模式（移动 / 加原子 / 连键 / 删原子）、点击加原子、
    按住拖动移动、`重置为直链`、`应用到画布` → 走会话 `apply`，
    因此**骨架编辑也能撤销/重做**（复用阶段②的历史栈）。
- **接口修正**：`GET /api/session` 现在与 POST 同构，**返回当前文档** `document`
  （客户端靠它读参数，骨架编辑器依赖这一点；原先只返回 session 概览）。
- 测试 198 → **215**（`test_skeleton.py` 15 条 + 会话文档 2 条）；浏览器回归 17 → **18 项**。


- **阶段③ 续：两种新内置图类型**（内置图 4 → **6 种**，达到契约要求）：
  * `enthalpy` **燃烧焓对比条形图**：五种醇的班级数据 vs 文献（默认 `kJ/g`），
    可显示百分比；默认数据就是本次报告的实测/文献值（8.93/29.7 … 16.69/39.0）。
  * `esterification` **酯化反应图**：羧酸 + 醇 --浓硫酸/加热--> 酯 + 水，
    醇用与其它图相同的锯齿链（氧红色 + 孤对电子），酸/酯/水用富文本下标（不用 Unicode）。
  * **CLI 子命令自动生成**：凡是没有手写子命令的图类型（含**插件图**）
    都按 `api.FIGURES` 自动生成 `--参数` 选项，新图类型零 CLI 改动即可用。
  * `make figures` 现在出 6 张示例图；测试 192 → **198**。
  * 如实记录一个已知告警：`series` 在自适应重绘后会命中"找不到全白分隔线"的启发式
    （P3 待办：改为墨迹包围盒相交判断），测试已按此如实断言。


- **阶段④（完成）自定义图类型：插件机制**（见 ADR-011）：
  * **Python 插件**：`plugins/*.py` 里 `def register(api)` → `api.add_figure(kind, runner, …)`，
    门面暴露 `surface` / `core` / `figures` / `int_param` 等稳定工具（与内置图同一套底层）。
  * **JSON 预置**：`*.figure.json` 把内置图类型配一套默认参数另起名字（如"丁醇报告版"）。
  * 扫描位置：设置里的 `pluginsDir`（默认配置目录 `plugins/`）+ 仓库根 `plugins/`；
    `CHEMFIG_PLUGINS=<dir>[:<dir>]` 显式指定，`off` 完全关闭（测试默认 off）。
  * 三条保护（均有测试）：**内置图类型不可被覆盖**、**坏插件只记错误不拖垮程序**、
    插件图类型排在参数表**末尾**保持内置顺序。
  * 入口：`chemfigstudio plugins`、`doctor` 的「插件图类型」、`GET /api/plugins`；
    插件图自动出现在 GUI 的类型下拉里（无需改前端）。
  * 附带示例：`plugins/example_chcount_bar.py`（Python）与 `plugins/example_preset.figure.json`（JSON）。
- 测试 176 → **192**。


- **阶段③ 第一增量：批量导出的图形界面**：界面新增「批量导出…」面板 ——
  选 CSV 文件或直接粘贴、`预览行`（干跑，列出将写入的文件名，**不落盘**）、
  `全部导出`（有同名文件时**先问再写**）、逐行结果列表（✓/✗）。
  接口 `POST /api/batch`（`dryRun`）；`batch.read_csv_text()` 让浏览器直接发 CSV 文本（免 multipart），
  `batch.target_paths()` 干跑算路径。测试 167 → **176**；浏览器回归 16 → **17 项**。
- **ADR-010：不做代码签名/公证**：保持 ad-hoc 签名，README 写明两种放行方式
  （右键打开 / `xattr -dr com.apple.quarantine`）；相应待办关闭。


- **输入即刷新（ 达标）**：改任意参数后 **250 ms 防抖**自动重画预览（实测 **306 ms ≤ 500 ms**），
  只重画、不导出、不落用户输出目录。做法：`renderFields()` 给每个控件直接绑 `input`/`change`，
  并暴露 `window.chemfigLiveFires` 计数器供浏览器回归断言"事件确实到达"。
- **P2 mypy / black 配置**：`pyproject.toml` 增加 `[tool.mypy]`（python_version 3.9、`check_untyped_defs`、
  排除 `legacy/ dist/ .pylibs/`）与 `[tool.black]`（line-length 100、py39）；
  `make typecheck` / `make format` 会优先用 `./.pylibs` 里的工具（与 RDKit 自动发现同一套思路），
  没装则打印 SKIPPED 提示。**mypy 实跑并把 10 个类型错误全部修掉**（现为 `Success: no issues found in 16 source files`）。


- **P2 外壳增强**：拖 `.smi`/`.csv`/文本进窗口 → **自动填充**（CSV 会跳过表头，取 name/smiles/ΔH）
  并立即刷新画布；新增「复制图片」按钮（`navigator.clipboard.write` + `ClipboardItem`，
  失败返回原因不抛异常）；**记住布局**（窗口尺寸 + 上次图类型防抖写入设置的 `windowState`，
  启动时恢复上次图类型）。测试/外壳菜单可调 `window.chemfigApplyText` / `window.chemfigCopyImage`。
- **P2 通用二进制**：`make bundle-universal` —— 分别以 `-target arm64-apple-macos11` 与
  `-target x86_64-apple-macos11` 编译，再用 `scripts/make-universal.sh` 以 `lipo -create` 合并成
  `dist/ChemFigStudio-universal.app`。**实测验证**：`lipo -archs` 输出 `x86_64 arm64`。
  （签名仍是 ad-hoc：macOS 26 已移除 `spctl --add`，要免"未知开发者"拦截需开发者证书公证。）
- 测试 166 → **167**；浏览器回归 13 → **15 项**（新增拖放填充、剪贴板接口，0 console 错误）。


- **P1 RDKit 自动发现**：`smiles.ensure_rdkit_path()` 依次探测 `CHEMFIG_PYLIBS`（冒号分隔多个）
  → 仓库根 `.pylibs` → 当前目录 `.pylibs`，命中含 `rdkit` 包的目录才前插 `sys.path`（幂等）；
  新增 `rdkit_source()`，`doctor` 直接显示 RDKit 来自哪里。用户不必再手动设 PYTHONPATH。
- **P1 批量出图**：`chemfigstudio batch molecules.csv --outdir out [--kind series] [--format svg]
  [--dpi 300] [--filename-template "{index:02d}_{name}"] [--json]`。
  `batch.py`：CSV 表头大小写不敏感、兼容中文列名（名称/焓变/碳数）与 UTF-8 BOM、`#` 注释；
  **单行失败不中断**（好的照常出图，坏的记行号与原因），warnings 全批汇总，全成功退出码 0 / 有失败为 2。
- 测试 150 → **166**（`test_batch.py`：CSV 解析 6 条 + 批量执行 6 条 + RDKit 探测 5 条等）。


- **阶段②（进行中）编辑会话：多标签 + 撤销/重做 + 图层面板**（/5/6）：
  `history.py` 的 `DocumentSession`（每标签独立文档与历史、快照式历史默认 100 步、
  新编辑丢弃 redo 分支、`snapshot()/restore()` 支持崩溃恢复）；
  `POST /api/session`（newTab/closeTab/select/apply/undo/redo/addLayer/toggleLayer，全部只改内存不落盘）；
  界面新增标签条、图层面板（含可见性勾选与添加图层）、撤销/重做按钮，「生成」改为先 `apply` 记历史再刷新画布。
  测试 133 → **150**；浏览器回归 9 → **12 项**。


- **预览图过大**：`api.preview()` 原先走 DPI 目标逻辑，150 dpi 也出 3807×1408（拖慢界面）。
  改为 **1:1 绘制 + 最长边封顶 1200 px**（超出时按比例重绘）；新增 3 个测试。


- **i18n 共享可变状态（潜在竞态）**：按语言取词改为**纯函数** `i18n.text(key, lang)`
  （原 `resolve_text` 会 set→取→restore 全局语言）；`t()` 成为它的薄封装。
  新增测试：取词不改全局语言、`list_figures(lang)` 无副作用、6 线程 ×200 次并发压测不串语言。
- **前端弹层盖住界面**：`.sheet { display:flex }` 压过了 HTML `hidden` 属性，
  设置/导出面板一直显示并罩住整页（真浏览器回归抓到的）。修 `.sheet[hidden]{display:none}`，
  并由 `test_frontend_wires_i18n_and_settings` 锁死。

### Added

- **`make browser-check`**：真浏览器回归（ego-browser），覆盖 JS 无法用 unittest 覆盖的部分 ——
  表单填充、**点生成出预览**、语言切换生效、导出对话框字段、刷新后草稿恢复、console 错误。
  已跑通 9/9、0 console 错误。

- **阶段②（进行中）文档模型 + 预览 + 草稿**（/2/3 的模型层）：
  `document.py`（`*.cfgd.json`，`formatVersion` 只增不改、高版本拒绝打开、未知字段保留、
  单分子/图两种模式、图层可见性）；`api.preview()` 把画布预览写到**配置目录的 preview/**，
  **用户输出目录保持零新增文件**（有测试做目录快照 diff）；草稿存 `drafts/`，
  界面启动时自动恢复最近草稿；网页界面的「生成」已切换为预览（不落盘）。
- 测试 112 → **130**（`test_document.py` 12 条 + GUI 预览/草稿 4 条等）。

- **阶段① 完成：导出对话框 + `POST /api/export`**（、12、13）：
  目标文件已存在且未确认时**先问再写**（取消则 mtime 不变）；目录不可写给出可读错误且不留半成品；
  网页界面新增「导出图片…」对话框（目录/文件名/格式/DPI/透明背景 + 覆盖确认）。
- **阶段①（进行中）导出管线：PNG / SVG / PDF**：新增双后端画布
  `canvas.Surface`（一份绘制指令同时产出位图与矢量）—— **SVG 是真文本**（可选中，非路径），
  PNG 与 SVG 共用同一取景（`viewBox` 换算，）；PDF 由 Pillow 输出（零新依赖）。
  公开入口 `api.render(..., formats=..., dpi=..., transparent=...)`，
  CLI 新增 `--format/--dpi/--transparent`（也可从 `-o` 扩展名推断）；
  高分辨率 = **按目标像素重绘**（自适应两遍），不是位图插值。
- 测试 96 → **106**（`test_export.py`：三格式产出、只要求 svg 时主路径正确、
  SVG 真文本且无位图、viewBox 与 PNG 一致、PDF 页面尺寸 <1%、DPI 影响像素、
  透明 PNG 四角 alpha=0、四种图 × 三格式矩阵）。

- **阶段①（进行中）i18n 全链路接线**：`api.list_figures(lang)` 输出本地化标题/标签/选项
  （`param.*`/`figure.*` 为 key，插件也可直接写死文本）；CLI 新增全局 `--lang`（help 也用该语言生成）
  与 `settings` 子命令（查看/修改/`--reset`/`--json`）；GUI 新增
  `GET/PUT /api/settings`、`GET /api/i18n/<lang>`、`/api/figures?lang=`；
  网页界面加**语言下拉**与**设置面板**（输出目录/语言/默认格式/DPI/透明背景），
  全部文案走 catalog（HTML 用 `data-i18n`，JS 用 `t()`）。
- **错误路径**：设置文件写不了时抛 `ConfigError` → CLI 输出可读消息 + 退出码 3（不再是 traceback）。
- 测试 80 → **95**（新增：英文 help/结果/错误消息无残留中文、settings 子命令往返与默认输出目录、
  GUI 设置接口往返、英文 payload 全量扫描 CJK、前端契约）。

- **阶段①（进行中）设置系统** `settings.py`：输出目录 / 语言 / 默认格式·DPI·透明背景 / 插件目录 /
  最近文档；未知字段保留、损坏自动备份并回默认值、原子写入（临时文件 + `os.replace`）。
  位置：macOS `~/Library/Application Support/chemfigstudio/settings.json`，
  其它平台 `~/.config/...`，`CHEMFIG_CONFIG_DIR` 可覆盖（测试/便携）。
- **阶段①（进行中）国际化** `i18n.py` + `locales/{zh-CN,en}.json`：101 条 key，
  中英 key 集合由测试强制一致；回退链 当前语言→en→key；占位符 `{name}`；
  新增语言 = 加一个 JSON 文件（为 R3「第三语言」预留）。
- 测试 +29：`test_settings.py`（位置/往返/未知字段/损坏自愈/校验/原子写/最近文档）、
  `test_i18n.py`（目录一致性/语言别名/系统语言/占位符/回退）。

- **macOS 原生应用**（`native/macos/ChemFigStudio.swift`）：Swift + WKWebView 外壳，
  独立窗口、Dock 图标、原生菜单（重新载入 / 打开输出目录 / 关于），
  自动挑空闲端口启动本地服务，退出时清理子进程。
  构建：`make bundle`（内嵌 Pillow ≈13 MB）或 `make bundle-lite`（≈400 KB）；
  打开：`make app` / 双击 `scripts/launch-app.command`。
- **App 图标**：`scripts/make-icon.py` 用项目自己的绘图代码生成 `.icns`，风格与输出一致。
- **父进程看门狗**：`gui/server.py --parent-pid` —— 外壳被强杀时服务自己收尸（有集成测试）。
- `gui/server.py --quiet`（不打印启动横幅，供外壳使用）。

- **桌面版应用**（`gui/tk_app.py`，tkinter + ttk `aqua` 主题）：原生窗口、
  窗口内预览、状态栏与告警行、一键「打开网页版」；
  入口 `make app` / `python3 -m chemfigstudio.cli app` / 双击 `scripts/launch-app.command`。
- **`gui/formmodel.py`**：前端无关的表单状态，桌面版与网页版共用，
  因此可以无头测试（`tests/test_formmodel.py`）。

### Fixed

- **`make bundle` 直接失败（回归）**：给构建脚本加 `--arch` 支持时用了 bash 数组，
  macOS 自带 bash 3.2 + `set -u` 下**空数组展开**会报 `TARGET_FLAG[@]: unbound variable`，
  于是不带 `--arch` 的普通构建全炸（且脚本先 `rm -rf` 旧 app → 目录被清空）。
  改用普通字符串拼接；新增回归测试：用**假 swiftc** 真跑一遍构建脚本，
  断言不出现 `unbound variable`、且 app 内嵌的是**当前**前端。
- **app 里看到旧界面**：打包是前端**快照**，改完 `gui/static/` 不重建，app 里就是旧代码 ——
  这正是「一打开卡在导出弹层」的原因（旧 bundle 缺 `.sheet[hidden]` 修复）。
  新增测试对比 `dist` 内嵌前端与源码，不一致就失败并提示 `make bundle`。


- **`series` 行重叠误报**（真 bug）：自检原先是「在设备像素图里找一条全白行」，
  却用**未缩放坐标**去索引 `scale > 1` 的图像 → 查错行，DPI 一放大就误报。
  改为**精确的行带墨迹外框检测**（`_series_overlap_warnings`）：只有相邻两行墨迹
  **都越过行间距中线**才告警，与缩放无关。实测 150/300/600 dpi（含自适应重绘）**全部无告警**。
  新增 4 条测试：各 DPI 不误报、合成真重叠必检出、分离行不告警、**缩放不变性**。


- **服务端 Optional 属性访问**（mypy 发现）：`GuiHandler.settings/session` 标注为 `Optional` 后，
  新增惰性 `cfg` 属性与 `_session()` 局部变量，避免 `None` 属性访问。
- 若干类型收窄：`draw_lone_pairs(font_size: float)`、`figures` 里 `int(float(str(...)))`、
  `canvas._pts` 显式 `List[Sequence[float]]`、`Path(str(...))`、`int(str(raw_dpi))`。
- 浏览器回归的 console 过滤器原本漏掉**未捕获异常**（`Runtime.exceptionThrown` 不含 "error"）——
  现在 `error|exception` 都抓，避免异常被静默吞掉。


- **HTTP 预览被缩小**：`/api/preview` 把 `dpi` 缺省当成 150（"放大目标"逻辑），
  而 150 dpi 对现有画布恰是 1:1 的 0.5 倍 → 浏览器里预览只有 197×106（糊）。
  改为缺省交给 `api.preview`（0 = 1:1 绘制 + 封顶 1200），实测回到 **541×229**；
  新增断言：300 ≤ 预览最长边 ≤ 1200（Python 侧 + 浏览器侧各一条）。


- **桌面版在 macOS 深色模式下整窗空白**：Tk 8.5 的 `aqua` 主题不支持深色模式，
  控件被画成不可见。改为 `clam` 主题 + 显式配色（`PALETTE`），并加启动置前
  （`lift` / 短暂 topmost / `focus_force`），保证首帧就绘制出来。


- `make lint` 不再用 `compileall`（会尝试写系统字节码缓存，在沙箱/只读环境下报
  `PermissionError`），改为 `ast.parse` 只读语法检查；Makefile 全局设
  `PYTHONDONTWRITEBYTECODE=1`。

## [0.2.0] — 2026-10-16

从单文件脚本重构为可分层的包，并加上本地图形界面。

### Added

- **本地网页 GUI**（`gui/server.py` + `gui/static/`）：选图类型 → 填参数 → 实时预览，
  告警以黄色提示条显示，可在 Finder 中定位输出文件。
  表单由 `GET /api/figures` **动态生成**，后端加参数无需改前端。
- **SMILES / 名称导入**（`--smiles CCCCO`、`--name butan-1-ol`）：
  内置直链伯醇解析器（零依赖）+ 可选 RDKit（支持支链/环，附分子式与 M）。
- **`doctor` 子命令**：打印 python / Pillow / 字体解析结果 / RDKit 状态。
- **`--json` 输出**：结果（path/size/warnings/smiles）以 JSON 返回，便于脚本与 agent 调用。
- **参数校验层**：`api.ParamSpec`（类型、范围、枚举）+ `RenderError`，
  错误信息面向用户（例如"碳原子数不能大于 12"）。
- **自动告警**：内容贴到画布边缘 → `SaveReport.warnings`（不再静默裁切）。
- **测试套件**：42 个 unittest，覆盖 core / figures / CLI / GUI（GUI 真起 HTTP 服务）。
- **文档**：`README.md`、`ARCHITECTURE.md`、
  `CONTRIBUTING.md`、`docs/STYLE_SPEC.md`、`docs/DECISIONS.md`（7 条 ADR）。
- **兼容 shim**：`tools/chemfig.py` 转发到新包，旧命令继续可用；
  原单文件版存档于 `legacy/chemfig_v1_original.py`。

### Changed

- 桌面端技术栈从 **tkinter 改为 Swift + WKWebView**（见 ADR-009）：Tk 8.5 在 macOS
  深色模式下整窗空白实测无解；Qt 在本机 Python 3.9 上装不了。界面仍只有一份网页。
- `chemfigstudio app` 子命令改为"打开原生应用（必要时先编译）"。

### Removed

- `gui/tk_app.py`、`gui/formmodel.py` 及其测试（Tk 方案作废后的死代码）。
- `scripts/make-app-bundle.sh`（旧的 shell-script 版 .app，被 Swift 外壳取代）。

- 单位写法改为面向 10 年级：`kJ/g`、`kJ/mol`（原为 `kJ g⁻¹`、`kJ mol⁻¹`）。
- 键名统一用连字符：`C-H`、`C-C`、`O-H`（原为 en dash）。
- 燃烧反应图的方程式**按碳数自动配平**（奇数碳时整条方程式乘 2）。
- `core` / `figures` 不再 `print`，改为返回 `SaveReport`；CLI 负责打印。
- 字体解析支持环境变量 `CHEMFIG_FONT` / `CHEMFIG_FONT_BOLD`（跨平台）。

### Fixed

- `save_figure` 用 `with Image.open(...)`，消除 `ResourceWarning: unclosed file`。
- `gui/__init__.py` 改为惰性导出，消除 `python3 -m chemfigstudio.gui.server`
  的 `RuntimeWarning: found in sys.modules`。
- `cli.main()` 不再抛 `SystemExit`（argparse 的退出被转为返回码），可安全嵌入测试。
- GUI 预览用 `?t=时间戳` 破缓存，同名文件重出后能看到新图。

## [0.1.0] — 2026-10-15

### Added

- 初版单文件脚本 `tools/chemfig.py`（192 行）：`alcohol` / `series` / `reaction` /
  `chcount` 四种图，Arial 字体、氧红色、孤对电子、手绘上下标、自动裁边、
  贴边与行重叠自检。
