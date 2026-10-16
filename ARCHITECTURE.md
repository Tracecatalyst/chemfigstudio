# 架构说明（ARCHITECTURE）

> 本文写给**要改这个项目的下一个人 / 下一个 agent**。读完你应该能：
> 知道每段代码在哪、为什么这样分层、改哪里最安全、哪些是绝对不能碰的。

---

## 1. 一句话定位

把"参数（碳数 / SMILES / ΔH）"变成"课本风格的 PNG 分子图"，
过程中不联网、不依赖化学渲染服务，唯一的第三方依赖是 Pillow。

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
                       │  figures.py                   │  四种图的构图逻辑
                       │  alcohol / series / reaction  │  （画什么、画在哪）
                       │  / chcount                    │
                       └───────────────┬───────────────┘
                                       ▼
                       ┌───────────────────────────────┐
                       │  core.py                      │  图元 + 文本 + 画布
                       │  字体 / 上下标排版 / 键 / 孤对 │  （怎么画）
                       │  电子 / 自动裁边 / 贴边检测    │
                       └───────────────┬───────────────┘
                                       ▼
                              Pillow → PNG 文件
```

**依赖方向是单向的**（`gui/cli → api → figures → core`）。
反向 import 会立刻形成环，禁止。

---

## 3. 模块职责表

| 文件 | 职责 | 依赖 | 明确**不做**的事 |
|---|---|---|---|
| `core.py` | 字体解析与缓存、`_{}`/`^{}` 标记解析与排版、画键/画孤对、画布、自动裁边、贴边告警 | Pillow | 不认识"醇"这种概念；不打印；不读命令行 |
| `figures.py` | 四种图的构图（标题、坐标、行距、ΔH 文案、方程式配平） | `core` | 不做参数校验；不 exit；不写日志 |
| `api.py` | 参数表 `ParamSpec`、图注册表 `FIGURES`、校验 + 类型转换、输出路径解析、`render()` | `figures`、`smiles` | 不画图；不碰 HTTP |
| `smiles.py` | SMILES / 名称 → 碳数（RDKit 可选，内置直链解析兜底） | `errors` | 不画图 |
| `errors.py` | `RenderError`（用户可纠正）/ `ConfigError`（环境问题） | 无 | —— |
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
# core.py
ChainStyle(n_carbons, label_style, label_size, sub_size, dx, dy, bond_width, note, …)
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
5. **`core.py` 不许出现化学语义**（"醇""燃烧"这类词），那些属于 `figures.py`。
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

### 7.4 想要矢量输出（SVG）

目前是 Pillow 位图。加 SVG 属于**换后端**：需要给 `core` 抽一层
"绘制接口"（现在直接调 `ImageDraw`），再实现一个 SVG 后端。
建议做法：先写 `docs/DECISIONS.md` 的一条 ADR，再动代码。

---

## 8. HTTP 接口契约（GUI）

只监听 `127.0.0.1`（默认端口 8765，被占用则 +1 顺延，最多试 12 次）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 返回 `static/index.html` |
| GET | `/static/<file>` | 静态资源（限制在 `gui/static/` 内） |
| GET | `/out/<file>` | 预览已生成的图（限制在 `--outdir` 内） |
| GET | `/api/health` | `{"ok":true,"version":…,"outdir":…,"figures":[…kind]}` |
| GET | `/api/figures` | 参数表：`{"ok":true,"figures":[FigureSpec.as_dict()…],"outdir":…}` |
| POST | `/api/render` | 见下 |
| POST | `/api/reveal` | `{"file":"x.png"}` → 在 Finder 中显示（macOS） |

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
| `test_core.py` | 标记解析、字体缓存、裁边尺寸、**贴边告警**、链长推进 |
| `test_figures.py` | 四种图都能出图 + PNG magic + 标准参数下**零告警**；反应方程式奇偶碳配平 |
| `test_cli.py` | **旧命令兼容**（硬约束）、`--json`、`--outdir`、`doctor`、错误码 2、SMILES 校验、名称解析 |
| `test_gui.py` | **真起 HTTP 服务**：health / figures / render 往返 / 预览取图 / 400 / 404 / 路径穿越拦截 |

原则：**改任何行为都先加/改测试**；GUI 测试不依赖浏览器，可在无界面环境跑。

---

## 11. 限制与技术债

* 只出 PNG（`docs/DECISIONS.md` ADR-004 说明为什么暂时不做 SVG）。
* 原生外壳**只有 macOS 版**（Windows/Linux 用浏览器版）；目前只编译当前架构
  （arm64），要通用二进制得加 `-target x86_64-apple-macos11` 再 `lipo`。
* 外壳未做代码签名与公证，拷给别人时首次打开需右键 →「打开」。
* `alcohol_figure` 的坐标是**硬编码**的（`1500×420`、链起点 `130,200`），
  不是真正的布局引擎；要更灵活的排版需要引入布局层。
* `series_figure` 的行重叠自检依赖"全白行"启发式，不是精确的包围盒相交判断。
* 内置 SMILES 解析只支持直链伯醇（`C…CO`）；支链/环需要 RDKit。
* 没有类型检查（mypy）与格式化（black）配置 —— 见项目 TODO。
