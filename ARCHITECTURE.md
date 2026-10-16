# 架构说明（ARCHITECTURE）

> 本文写给**要改这个项目的下一个人 / 下一个 agent**。读完你应该能：
> 知道每段代码在哪、为什么这样分层、改哪里最安全、哪些是绝对不能碰的。

---

## 1. 一句话定位

把"参数（碳数 / SMILES / ΔH）"变成"课本风格的分子图"，输出 **PNG / SVG / PDF**
（SVG 是真矢量文本，见 ADR-014）；过程不联网、不依赖化学渲染服务，
唯一的第三方依赖是 Pillow（RDKit 可选，见 ADR-006/ADR-012）。

---

## 2. 分层

```
   ┌────────────────────────┐   ┌──────────────────┐   ┌──────────────┐
   │ macOS 原生应用           │   │ 网页版            │   │ CLI          │
   │ native/macos/*.swift   │──▶│ gui/server.py    │   │ cli.py       │
   │ (WKWebView 外壳，无逻辑) │HTTP│ (http.server)    │   │ (argparse)   │
   └────────────────────────┘   └────────┬─────────┘   └──────┬───────┘
            ▲ 起进程 + 开窗口             │ /api/render         │ 只调 api
            └────────────────────────────┴──────────┬──────────┘
                                                   ▼
                       ┌───────────────────────────────┐
                       │  api.py                       │  ← 唯一入口
                       │  参数表 / 校验 / 类型转换      │
                       │  SMILES 解析（委托 smiles.py） │
                       └───────────────┬───────────────┘
                                       ▼
                       ┌───────────────────────────────┐
                       │  figures.py                   │  七种图的构图逻辑
                       │  alcohol / series / reaction  │  （画什么、画在哪）
                       │  / chcount / enthalpy /       │  + 示性式的化学词汇表
                       │  esterification / skeleton    │  （chain_style）
                       └───────────────┬───────────────┘
                                       ▼
                       ┌───────────────────────────────┐
                       │  core.py                      │  图元 + 文本 + 画布
                       │  字体 / 上下标排版 / 锯齿链 /  │  （怎么画；不认识化学）
                       │  自动裁边 / 贴边检测           │
                       └───────────────┬───────────────┘
                                       ▼
                    canvas.Surface（双后端）→ PNG / SVG / PDF 文件
```

**依赖方向是单向的**（`gui/cli → api → figures → core`）。
反向 import 会立刻形成环，禁止。

**3D 是一条旁路**（ADR-012），不接入上面的 2D 链：

```
cli.py / gui/server.py ──> render3d.py ──> RDKit ──> molblock(文本)
                                                         │
   gui/static/viewer3d.js + vendor/3Dmol-min.js <────────┘  （浏览器渲染、导出 PNG）
```

所以：`core.py` / `canvas.py` / `figures.py` **一行都没为 3D 改动**；
3D 既不在 `FIGURES` 注册表里（不是"图类型"），也不进 `make figures` 与 CSV 批量。

---

## 3. 模块职责表

| 文件 | 职责 | 依赖 | 明确**不做**的事 |
|---|---|---|---|
| `core.py` | 字体解析与缓存、`_{}`/`^{}` 标记解析与排版、画键、画锯齿链、画点对、画布、自动裁边、贴边告警 | Pillow | **不认识任何化学概念**（标签由调用方注入，见 §6.5）；不打印；不读命令行 |
| `figures.py` | **七种**图的构图（标题、坐标、行距、ΔH 文案、方程式配平）+ 示性式化学词汇表 `chain_style()` | `core`、`layout` | 不做参数校验；不 exit；不写日志 |
| `api.py` | 参数表 `ParamSpec`、图注册表 `FIGURES`、校验 + 类型转换、输出路径解析、`render()` | `figures`、`smiles` | 不画图；不碰 HTTP |
| `smiles.py` | SMILES / 名称 → 碳数（RDKit 可选，内置直链解析兜底） | `errors` | 不画图 |
| `errors.py` | `RenderError`（用户可纠正）/ `ConfigError`（环境问题） | 无 | —— |
| `render3d.py` | SMILES → **3D 构象**（molblock）。薄封装 RDKit（ETKDGv3 + MMFF94），RDKit 缺失时抛 `ConfigError`（附安装命令） | `smiles`、`errors` | 不画图、不写文件、不打印、**不认识 2D 画布** |
| `gui/static/viewer3d.*` + `vendor/3Dmol-min.js` | 3D 查看器页面（浏览器侧渲染、旋转、导出 PNG）。3Dmol.js **自托管**，无 CDN | 浏览器（3Dmol.js） | 不做 chemistry 计算（几何全来自 `/api/render3d`） |
| `cli.py` | argparse、参数映射、结果打印（含 `--json`）、`doctor`、`gui`/`app` 子命令 | `api`、`core` | 不直接调 `figures`/`core` 画图 |
| `gui/server.py` | HTTP 路由、静态文件、JSON API、路径安全、端口回退、父进程看门狗 | `api` | 不重新实现业务逻辑（只转发到 `api.render`） |
| `native/macos/ChemFigStudio.swift` | **macOS 外壳**：挑空闲端口、拉起服务、WKWebView 显示页面、退出时杀子进程 | 无（只看 HTTP） | **不许写业务逻辑**（有测试断言源码里不出现 `figure`/`kJ/g` 等字样） |
| `gui/static/*` | 网页版前端（无框架、无 CDN）；表单**由 `/api/figures` 动态生成** | 浏览器 | 不硬编码参数（后端加参数前端零改动） |

**界面只有一份**：网页（`gui/static/`）。原生应用只是把它装进 WKWebView 窗口，
所以"网页版有什么，应用就有什么"。要加功能，改 `api.py` 的参数表 + `figures.py`
即可，两个入口同时生效 —— 任何"只在一个界面里做"的逻辑都是 bug。

---

## 4. 一次渲染的完整数据流

以 `GUI 里选 alcohol、填 SMILES=CCCCO` 为例：

1. 前端 `POST /api/render`，body：`{"kind":"alcohol","params":{"smiles":"CCCCO","n_carbons":"4",...},"filename":"out.png"}`
2. `GuiHandler._handle_render` 校验文件名（`_safe_child`，禁止 `/`、`..`），拼出 `outdir/out.png`
3. `api.render("alcohol", out=…, params=…)`
   - `FIGURES["alcohol"].prepare(params)`：逐个字段按 `ParamSpec` 校验/转型（字符串 → int、范围检查、枚举检查）
   - 发现 `smiles` 非空 → `smiles.resolve_smiles("CCCCO")` → `SmilesInfo(n_carbons=4, name="butan-1-ol", source="builtin")`
   - `smiles` 字段从 kwargs 里剔除，碳数写回 `n_carbons`
4. `resolve_output_path()` 决定最终路径
5. `alcohol_figure(path, n_carbons=4, name=…, delta_h=…, label_style=…, title=…)`
   - `core.new_canvas(1500, 420)` → 纯白画布
   - `core.draw_rich_centred(...)` 画标题
   - `core.draw_chain(draw, 130, 200, ChainStyle(n_carbons=4))`：算锯齿顶点 → 画键 → 画 `CH_{3}` → 画转角 `H_{2}` → 画 `OH`（红）+ 孤对电子，返回右端 x
   - `core.save_figure(image, path)`：先做**贴边检测**（有内容贴边 → 记 warning），存盘，按墨迹包围盒裁边留 26 px 白边，回读尺寸
6. 逐层返回 `FigureResult(kind, SaveReport(path,size,warnings))`，`.as_dict()` 变 JSON
7. 前端把 `url` 塞进 `<img src=…?t=时间戳>` 破缓存，渲染 warnings 变成黄色告警条

**关键设计**：`SaveReport.warnings` 一路向上传到 JSON，所以"图被裁了"这种事
不会静默发生 —— GUI 会显示，CLI 会打印，测试会断言。

---

## 5. 核心数据结构

```python
# core.py —— 只有几何与"要写什么字"，字由调用方给
ChainStyle(n_carbons, label_size, sub_size, dx, dy, bond_width,
           head_label, vertex_label, end_label, end_colour, end_lone_pairs,
           note, note_label, note_head_label, …)
SaveReport(path, size, warnings, cropped)          # .as_dict()

# figures.py
FigureResult(kind, report, smiles_info)            # .path/.size/.warnings/.as_dict()

# api.py
ParamSpec(name, type, label, default, choices, minimum, maximum, help)
   # type ∈ {int, str, choice, bool, smiles}；.coerce(value) 负责校验
FigureSpec(kind, title, description, params, runner, default_filename)
   # .prepare(params) 校验+转换；.as_dict() 给前端生成表单

# smiles.py
SmilesInfo(smiles, n_carbons, name, formula, mw, source, notes)
```

**文本标记语法**（`core.parse_markup`）：`H_{2}` 下标、`g^{2}` 上标，
只支持这两种；其它字符原样输出。这是全部化学式排版的基础。

---

## 6. 不变量（Invariants）—— 改代码时不能破

1. **上下标必须手绘**，绝不在字符串里放 Unicode 上下标（`₂` `⁻¹` …）。
   原因：Arial 缺这些码位，Word/PNG 里会显示空心方块。
2. **单位用 `kJ/g`、`kJ/mol`**，不用负指数（使用者是 10 年级）。
   键名用连字符 `C-H` / `C-C` / `O-H`（与报告正文一致）。
3. **颜色固定**（`core.py` 顶部常量）：氧 `#D61F1F`、正文 `#111827`、
   说明 `#788291`、ΔH `#BE123C`、背景纯白。
4. **先大画布后裁边**：所有图都在超大画布上画，再由 `save_figure` 裁到墨迹 + 26 px。
   画布尺寸给小了会被切 —— 靠 `warnings` 发现，不要靠肉眼。
5. **`core.py` 不许出现化学语义**（"醇""燃烧"这类词，以及 `CH_{3}`/`OH` 这类字样），
   那些属于 `figures.py`。示性式的标签通过 `figures.chain_style()` 注入
   `core.ChainStyle` 的 `head_label`/`vertex_label`/`end_label` 字段。
   **有测试盯着**：`tests/test_core.CoreKnowsNoChemistryTests` 用 AST 扫 core 源码里
   非 docstring 的字符串与标识符名（按词边界匹配，避免 `"OH"` 命中 `draw_rich`）。
6. **GUI 不许有业务逻辑**，只转发 `api`；前端参数表单必须由 `/api/figures` 生成。
7. **公开 API 只增不改**：`api.render` / `figures.*_figure` / `core.*` 的签名
   要保持向后兼容（旧 CLI 命令是硬约束，见 `tests/test_cli.py::BackwardCompatibilityTests`）。

---

## 7. 扩展指南

### 7.1 加一个参数（最常见，只改 1 处）

在 `api.py` 对应 `_spec_*` 的 `params` 里加一条 `ParamSpec`，
然后在同一文件的 `run()` 里透传给 `figures.*`：

```python
ParamSpec("show_title", "bool", "显示标题", default=True)
```

GUI 会自动出现这个控件（表单是动态生成的），CLI 需要的话在 `cli.py`
`_params_from_args()` 里加一行 argparse 参数。**测试请同时在
`tests/test_gui.py::test_figures_spec_is_gui_ready` 覆盖。**

### 7.2 加一种新图（3 步）

1. `figures.py` 里写 `myfigure_figure(out, **params) -> FigureResult`（用 `core` 的图元）
2. `api.py` 里加 `_spec_myfigure()` 并注册进 `FIGURES`
3. `cli.py` 里加子命令（可选）；`tests/test_figures.py` 加一个用例

前端、`/api/figures`、`make figures` 都会自动适配。

### 7.3 换字体 / 跨平台

`core.FONT_CANDIDATES` / 环境变量 `CHEMFIG_FONT`、`CHEMFIG_FONT_BOLD`。
`make doctor` 会打印实际解析到的字体路径。

### 7.4 矢量输出（SVG）—— 已实现，加图元时要顾两个后端

`canvas.Surface` 就是那层"绘制接口"：`svg=True` 时同一份绘制指令同时产出
位图与 SVG 元素（文字是真 `<text>`）。**新增图元方法时必须在两个后端都实现**，
否则 SVG 会静默缺元素；`tests/test_export.py` 覆盖三种格式。
（决策过程见 ADR-014 —— 它取代了早期那条"暂不支持矢量"的 ADR-004；
历史措辞只保留在 `docs/DECISIONS.md` 里，本文件不再复述。）

---

## 8. HTTP 接口契约（GUI）

只监听 `127.0.0.1`（默认端口 8765，被占用则 +1 顺延，最多试 12 次）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 返回 `static/index.html` |
| GET | `/static/<file>` | 静态资源（限制在 `gui/static/` 内，**允许 `vendor/` 子目录**） |
| GET | `/out/<file>` | 预览已生成的图（限制在 `--outdir` 内） |
| GET | `/viewer3d` | 3D 查看器页面（独立页；3Dmol.js ~0.5 MB 只在需要时加载） |
| GET | `/api/health` | `{"ok":true,"version":…,"outdir":…,"figures":[…kind]}` |
| GET | `/api/figures` | 参数表：`{"ok":true,"figures":[FigureSpec.as_dict()…],"outdir":…}` |
| POST | `/api/render` | 见下 |
| POST | `/api/reveal` | `{"file":"x.png"}` → 在 Finder 中显示（macOS） |
| POST | `/api/render3d` | `{"smiles":"CCO"}` → **3D 几何数据**（molblock + 分子式/能量/方法）。缺 RDKit → **503**（不是 400） |
| GET | `/preview/<file>` | 编辑器的**不落盘预览**（在配置目录里，不写用户输出目录） |
| GET | `/api/documents` | 草稿列表（崩溃/关窗后可恢复） |
| GET | `/api/documents/<id>` | 读取某份草稿 |
| GET | `/api/plugins` | 已加载插件与错误（`chemfigstudio plugins` 的 HTTP 版） |
| GET | `/api/i18n/<lang>` | 文案目录 catalog（前端 `t()` 用它；`auto` = 当前语言） |
| POST | `/api/preview` | 画布预览（同上，只落配置目录） |
| POST | `/api/export` | 导出 PNG/SVG/PDF；**写盘根受 `allowedWriteDirs` 限制**（ADR-013） |
| POST | `/api/batch` | CSV → 多张图；`dryRun` 只回报将写哪些文件（供覆盖确认） |
| POST | `/api/session` | 多标签会话：新建/关闭/切换/撤销/重做/应用参数 |
| POST | `/api/documents` | 保存草稿 |
| PUT | `/api/settings` | 更新设置（与 POST 同处理） |

`POST /api/render` 请求：

```json
{"kind": "alcohol", "params": {"smiles": "CCCCO", "delta_h": "-36.1"}, "filename": "butanol.png"}
```

成功（200）与 :meth:`FigureResult.as_dict` 一致，另加 `ok` / `file` / `url` / `smiles`：

```json
{"ok": true, "path": "/abs/out/butanol.png", "file": "butanol.png", "url": "/out/butanol.png",
 "size": [649, 371], "warnings": [], "cropped": true, "kind": "alcohol",
 "smiles": {"smiles": "CCCCO", "n_carbons": 4, "name": "butan-1-ol", "source": "builtin"}}
```

失败：**400** + `{"ok":false,"error":"<用户可读原因>"}`（参数非法、SMILES 不是醇、文件名越界），
**500** + 同样结构（内部异常，stderr 里带 traceback，用 `--verbose` 看）。

**写盘边界**（ADR-013）：`/api/export` 与 `/api/batch` 的 `dir` 只能落在「输出目录」或设置里的 `allowedWriteDirs` 内，越界返回 **403** —— 本地服务收的是浏览器请求，不能让它把文件写到任意位置。所有改写请求还必须通过同源 `Origin` / 回环 `Host` / `application/json` 三道校验。

**安全约束**：所有用户提供的文件名都过 `_safe_child()`（拒绝 `/`、`\`、`..`、隐藏文件名，
并 `resolve()` 后校验仍在 outdir 内）。有对应测试
（`test_gui.py::test_path_traversal_is_blocked`）。

---

## 9. 退出码（CLI）

| 码 | 含义 |
|---|---|
| 0 | 成功（`--help` / `--version` 也是 0） |
| 1 | 保留给未预期异常 |
| 2 | **参数错误**：`RenderError`（碳数越界、SMILES 不是醇、未知参数、未知子命令） |
| 3 | **环境错误**：`ConfigError`（找不到字体等） |

`cli.main()` **不抛 SystemExit**（内部把 argparse 的退出转成返回码），
所以可以安全地在测试/其它程序里调用。

---

## 10. 测试策略

`make test` → `python3 -m unittest discover -s tests`（**不需要**安装任何东西，靠 `PYTHONPATH=src`）。

| 文件 | 覆盖点 |
|---|---|
| `test_core.py` | 标记解析、字体缓存、裁边尺寸、**贴边告警**、链长推进、**core 无化学语义（AST 守卫）** |
| `test_figures.py` | 七种图都能出图 + PNG magic + 标准参数下**零告警**；反应方程式奇偶碳配平；示性式词汇表 |
| `test_layout.py` | 声明式布局（`Slot`/`RowLayout`/`BannerLayout`）与 `BANNERS` 登记 |
| `test_skeleton.py` | 骨架模型/吸附/键级/多分子/元素切换 |
| `test_export.py` | PNG/SVG/PDF 三格式、覆盖确认、**文档不得声称只支持 PNG** |
| `test_batch.py` | CSV 解析、批量出图、`--watch`、RDKit 路径发现 |
| `test_render3d.py` | 3D 构象契约（mock RDKit）与 molblock 解析 |
| `test_settings.py` | 设置读写/校验/损坏恢复 |
| `test_document.py` / `test_history.py` / `test_plugins.py` / `test_i18n.py` | 草稿、撤销重做、插件、双语 key 集合一致 |
| `test_cli.py` | **旧命令兼容**（硬约束）、`--json`、`--outdir`、`doctor`、错误码 2、SMILES 校验、名称解析 |
| `test_gui.py` | **真起 HTTP 服务**：health / figures / render 往返 / 预览取图 / 400 / 404 / 路径穿越拦截 |
| `test_native_app.py` | 原生外壳：Swift 源码自检（含"不许有业务逻辑"）、构建脚本语法、已构建产物的 plist/内嵌源码/体积、**父进程看门狗**（父死子退）、`--parent-pid`/`--quiet` 存在 |

原则：**改任何行为都先加/改测试**；GUI 测试不依赖浏览器，可在无界面环境跑。

---

## 11. 限制与技术债

* 2D 输出 PNG / SVG / PDF 三种格式（ADR-014；SVG 是真矢量文本）。
  3D 静帧只有 PNG（ADR-012）。
* 原生外壳**只有 macOS 版**（Windows/Linux 用浏览器版）；目前只编译当前架构
  （arm64），要通用二进制得加 `-target x86_64-apple-macos11` 再 `lipo`。
* 外壳未做代码签名与公证，拷给别人时首次打开需右键 →「打开」。
* `alcohol_figure` 的坐标是**硬编码**的（`1500×420`、链起点 `130,200`），
  不是真正的布局引擎；要更灵活的排版需要引入布局层。
* `series_figure` 的行重叠自检依赖"全白行"启发式，不是精确的包围盒相交判断。
* 内置 SMILES 解析只支持直链伯醇（`C…CO`）；支链/环需要 RDKit。
* **3D 静帧只有 PNG**：3Dmol 走 `canvas.toDataURL()`，没有矢量输出（与 2D 的 SVG 双后端无关）。
* **3D 不进批量/示例图**：它不是 `FIGURES` 里的图类型，`make figures` 与 CSV 批量都不涉及它。
* **内嵌 rdkit/numpy 是编译扩展**：与 Python 版本 + CPU 架构绑定。内嵌版 app ≈ 113 MB；
  通用二进制在另一种 CPU 的 Mac 上 3D 不可用（2D 出图不受影响）。
* 3D 的自动测试只覆盖到"服务端回几何数据"这一层；浏览器侧（WebGL）靠手工端到端验证，
  因为 CI 里没有 GPU/浏览器（见 `docs/HANDOVER.md` 的验证小节）。

* 没有类型检查（mypy）与格式化（black）配置 —— 见 `AGENTS.md` 的 TODO。
