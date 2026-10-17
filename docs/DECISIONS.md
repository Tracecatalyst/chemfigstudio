# 架构决策记录（ADR）

> 记录**为什么这样做**，避免下一个人重复讨论或无意推翻。
> 格式：背景 / 决策 / 理由 / 后果 / 备选方案。
> **要推翻已有决策，先加一条新 ADR 说明取代关系**，不要直接改旧条目。

---

## ADR-001 —— GUI 用「本地网页界面」而不是 tkinter / PyQt

**背景**：需要图形界面，用户环境是 macOS + 系统 Python 3.9.6。
本机 `tkinter` 可用（Tk 8.5），PyQt 未装。

**决策**：用标准库 `http.server` 起一个只监听 `127.0.0.1` 的本地服务，
前端用原生 HTML/CSS/JS（无框架、无 CDN）。双击 `scripts/launch-gui.command`
即启动并自动打开浏览器。

**理由**：

1. **零依赖**：不引入 Qt（~100 MB）或新的 pip 包；`tkinter` 的 Tk 8.5 在 macOS 上
   外观老旧且中文字体/暗色模式坑多。
2. **可测试**：HTTP 接口能用 `urllib` 端到端断言（`tests/test_gui.py`），
   不需要显示器、不需要模拟点击；GUI 逻辑因此也能进 CI。
3. **可交接**：前端就是三个静态文件，任何 agent 都能读懂；表单由 `/api/figures`
   驱动，后端加参数前端零改动。
4. **预览天然免费**：图片本来就要在浏览器里看，`<img>` + 破缓存时间戳即可。

**后果**：

* 用户需要"双击一个文件"而不是"一个原生窗口"——已在 README 说明。
* 需要自己处理安全（路径穿越、只绑 127.0.0.1）——已有 `_safe_child()` 与测试。
* 想要原生窗口的话，因为业务逻辑全在 `api.py`，加一个 Tk 前端只需实现"表单 → `api.render`"。

**备选**：tkinter（外观差、难测试）、PyQt6（重依赖）、Electron（更重）。

---

## ADR-002 —— 上下标手绘，禁止 Unicode 上下标字符

**背景**：第一版把 `kJ mol⁻¹` 直接写进图里，导出后发现显示成 `kJ mol□¹`。

**决策**：图内文本统一用 `_{}` / `^{}` 标记，由 `core.draw_rich()` 用小字号 +
基线偏移绘制；**永不**在字符串里出现 `₂ ⁻¹ ¹` 等字符。

**理由**：Arial（以及 Helvetica 等常见替代）缺少 U+207B `⁻`、U+2082 `₂` 等码位，
Pillow/Word 会渲染成 `.notdef` 空心方块。这是**已复现**的问题，不是理论顾虑。

**后果**：

* 任何新图都必须用标记语法；`docs/STYLE_SPEC.md §5` 是硬规范。
* 单位只能写 `kJ/g`（顺带符合 10 年级可读性，见 ADR-007）。

**备选**：换一个含这些码位的字体（会失去 Arial 与课本的一致性）。

---

## ADR-003 —— 用 Pillow 直接画，不用 matplotlib / 化学引擎

**背景**：需要"示性式 + 孤对电子"的图。候选：RDKit depict、Open Babel、
CDK、ChemDoodle Web、matplotlib 手工绘制。

**决策**：用 Pillow 自己画图元（线、圆点、文本），几何参数集中在 `core.py`。

**理由**：

1. **风格不匹配**：RDKit / Open Babel / CDK / ChemDoodle 都出骨架式，
   且**没有任何一个能画孤对电子**（RDKit 的 93 个绘图选项里 `lone`/`pair` 一个都没有，已实测）。
2. **可控**：课本示性式的排版（转角写 `H₂`、CH₃ 放左上、键连到 O）需要精确控制，
   交给通用渲染器反而要跟它的排版规则搏斗。
3. **轻**：matplotlib 能画，但它的文本/字体回退会引入自己的缺字问题，
   而 Pillow 的 `ImageFont.truetype` + `textlength` 行为完全可预测。

**后果**：坐标是硬编码的（见 `ARCHITECTURE.md §11`），
要做复杂分子排版需要先引入布局层。

**备选**：matplotlib（依赖更大、字体问题更多）；LaTeX + chemfig（需要整个 TeX 环境）。

---

## ADR-004 —— 目前只输出 PNG，不做 SVG  **（已被 ADR-014 取代，2026-10-16）**

> 这条决策只对 v0.2 有效。SVG 与 PDF 后来都实现了（见 ADR-014），
> 本条目保留仅作历史记录 —— 别再按它写代码或写文档。

**背景**：报告是 Word 文档，PNG 够用；但矢量图放大不糊，长期看更理想。

**决策**：v0.2 只出 PNG（`Image.save`）。

**理由**：Pillow 是位图库，出 SVG 需要**换渲染后端**（抽象出绘制接口 →
实现一个 SVG writer），属于结构性改动，收益在当前场景（Word 插图、300 dpi 以内）不明显。

**后果**：需要 SVG 时按 `ARCHITECTURE.md §7.4` 的路径做，并先补一条新 ADR。

**备选**：`svgwrite` 手写（同样要抽象接口）；改用 `cairosvg` 双向（新依赖）。

---

## ADR-005 —— 保留 `tools/chemfig.py` 作为兼容 shim

**背景**：项目从单文件 `chemfig.py`（192 行）重构为包 `chemfigstudio`。
用户的 `README_文件夹说明.md` 里记着 `python3 ../tools/chemfig.py series -o …`。

**决策**：把 `tools/chemfig.py` 改写为 **shim**（把 `chemfig-studio/src` 插入
`sys.path` 后调 `chemfigstudio.cli.main`），原文件存档到 `legacy/chemfig_v1_original.py`。

**理由**：外部文档（练习目录 README）已经引用该路径，直接删除会让命令静默失效；
shim 只有 20 行，维护成本极低。

**后果**：新增 `tests/test_cli.py::BackwardCompatibilityTests` 锁住旧命令行为。
**HARD 约束**：旧命令的参数名（`--n`、`--dh`、`--vertex-labels`、`-o`）不能改。

**备选**：直接删旧文件并改文档（会让用户已有的笔记失效，否决）。

---

## ADR-006 —— SMILES 走「内置直链解析 + 可选 RDKit」双路径

**背景**：用户想要"直接导入分子"。RDKit 未安装在系统 Python，
但项目目录里有一份 `./.pylibs/rdkit`（需手动 `PYTHONPATH`）。

**决策**：`smiles.py` 先试 RDKit（能处理支链/环/杂原子并给出分子式与 M），
不可用时回退到内置正则，**只接受直链伯醇** `C…CO`；两条路径都必须通过
"恰好一个氧且是 -OH" 的安全检查。

**理由**：用户的真实用例（乙醇～己醇）是直链，内置解析就能覆盖，
不该为了 SMILES 强推一个 100 MB 的依赖；但一旦可用，RDKit 明显更强。

**后果**：错误信息必须写清楚"为什么不行、怎么装 RDKit"（已实现）。
*TODO（P1）*：支持 `CHEMFIG_PYLIBS` 自动把本地 `.pylibs` 加入 `sys.path`。

**备选**：强制依赖 RDKit（增加安装门槛）；只用内置（无法处理支链）。

---

## ADR-007 —— 单位与用词面向 10 年级

**背景**：使用者是 课堂 Year 5 学生；原图用了 `kJ g⁻¹`、`kJ mol⁻¹` 这类负指数写法。

**决策**：单位一律 `kJ/g`、`kJ/mol`、`g/mol`、`g/mL`、`J/g °C`；
键名用连字符 `C-H`；避免"负指数""孤对电子以外的进阶记号"。

**理由**：用户明确反馈"负指数太高级（too advanced for grade 10）"；
同时这也是规避 ADR-002 缺字问题的最简办法。

**后果**：`ALCOHOL_SERIES`、文字常量与示例图都按此写；新增文案必须遵守。

---

## ADR-008 —— 在网页版之外，再加一个 tkinter 桌面版

> ⚠️ **已被 ADR-009 取代**：Tk 8.5 在 macOS 深色模式下整窗空白（本机实测），
> 桌面端改为 Swift + WKWebView 外壳。此条保留仅为记录当时的取舍。

**背景**：用户要"既能当 app 用，也能在本地网页里用"。ADR-001 已经选了网页版作为
主界面；本机 `tkinter`（Tk 8.5 + aqua 主题）与 `PIL.ImageTk` 都可用。

**决策**：再加一个 **tkinter 桌面版**（`gui/tk_app.py`），并且**抽出
`gui/formmodel.py` 作为两个前端共用的表单状态**。业务逻辑一律不进视图。

**理由**：

1. **用户明确要两种形态**：桌面版适合日常点选（独立窗口、不占浏览器标签页），
   网页版适合截图、远程、以及没有 Tk 的环境。
2. **tkinter 是标准库**：不新增依赖（符合"新依赖要写 ADR"的默认答案），
   aqua 主题在 macOS 上看起来是原生控件。
3. **共享 FormModel 是关键**：若两端各自实现校验/取值，行为必然漂移。
   把"当前选哪种图、参数值、出图"收敛到一个可无头测试的对象后，
   视图只剩"画控件 + 显示结果"，两端一致性由测试保证。
4. **可测试**：`FormModel` 与 `main(["--filenames-only"])` 不需要显示器；
   控件级测试用 `root.withdraw()` 建隐藏窗口，无显示时自动 skip。

**后果**：

* 新增参数时**不要**改两个前端：改 `api.py` 的 `ParamSpec`，
  桌面版表单按参数表动态重建，网页版表单由 `/api/figures` 动态生成。
* Tk 8.5 的限制要记住（没有 `ttk.Spinbox`；预览图必须留引用）。
* 想要真正的 `.app` 双击图标需 py2app（新依赖，见项目 TODO）。

**备选**：只做网页版（不满足"当 app 用"）；PyQt（重依赖且外观收益有限）。

---

## ADR-009 —— 桌面端改为 Swift + WKWebView 外壳（取代 ADR-008）

**背景**：ADR-008 用 tkinter 做了桌面版，但在用户机器上**整窗空白**。
实测原因：系统处于 **macOS 深色模式**，而系统自带的是 **Tk 8.5**（2010 年），
其渲染层与新版 macOS 不兼容；换 `clam` 主题 + 显式配色也无效。
换 Qt：`PySide6 6.12` / `PyQt6 6.11` 都要求 **Python ≥ 3.10**，本机是 3.9.6，
且查遍 PyPI 没有 macOS arm64 + cp39 的 wheel。用户同时要求"泛用"且"体积精简"。

**决策**：桌面端改成 **Swift + WKWebView 的原生外壳**（`native/macos/ChemFigStudio.swift`）：

* 外壳只做四件事：挑空闲端口 → 拉起 `chemfigstudio.gui.server` → 用 WKWebView 显示页面 → 退出时杀子进程。
* 界面**仍然是那一份网页**（`gui/static/`），不写第二套 UI。
* 服务端新增 `--parent-pid` 看门狗：外壳即使被 SIGKILL，服务也会自己退出。
* 打包：`scripts/build-macos-app.sh` 用系统 `swiftc` 编译，内嵌 `src/`（+ 可选 Pillow），
  `make bundle` ≈ 13 MB，`make bundle-lite` ≈ 400 KB（目标机自备 Pillow）。

**理由**：

1. **零第三方依赖**：只用系统自带 Cocoa/WebKit/swiftc，可执行文件 ~130 KB。
2. **泛用**：界面只有一份 → 网页版在 Windows/Linux 上用浏览器即可，功能与 macOS 应用完全一致；
   新增图类型/参数只改 `api.py`，Swift 一行都不用动。
3. **可测试**：外壳无逻辑 → 测试可以断言"Swift 源码里不许出现业务字样"，
   并用集成测试验证看门狗；界面逻辑本来就有 HTTP 端到端测试覆盖。
4. **深色模式/新版 macOS**：WebKit 是系统组件，跟随系统外观，不存在 Tk 那种渲染缺陷。

**后果**：

* 外壳仅 macOS；目前只编 arm64（要发 Intel Mac 需 `lipo` 通用二进制）。
* 用户机器需要 Xcode 命令行工具（本机已装，`/usr/bin/swiftc`）。
* 拷给别人的 `.app` 未签名/未公证，首次打开要右键 →「打开」。
* Tk 相关代码（`gui/tk_app.py`、`gui/formmodel.py`、旧 shell bundle）已删除。

**备选**：继续修 Tk（实测无解）；等 Python 升到 3.10+ 再用 Qt（体积 +120 MB）；
Chrome `--app` 窗口（依赖用户装 Chrome，不算真应用）；Electron（体积巨大）。

---

## 新增 ADR 的模板

```markdown
## ADR-0XX —— <一句话决策>

**背景**：<遇到什么问题，有什么约束>

**决策**：<我们决定做什么>

**理由**：<为什么；最好是可验证的事实，而不是偏好>

**后果**：<会带来什么限制/需要同步改什么>

**备选**：<考虑过但否决的方案与原因>
```

---

## ADR-010：不做代码签名/公证，接受"首次需手动放行"

* **状态**：已接受（2026-10-16）
* **背景**：`make bundle-universal` 已能产出 arm64 + x86_64 通用二进制。macOS 26 移除了
  `spctl --add`，因此 ad-hoc 签名的 `.app` 拷到**别人机器**上首次打开会被 Gatekeeper 拦。
  彻底免拦截需要 Apple 开发者证书（$99/年）+ 公证（notarization）。
* **决定**：**项目不引入证书**。保持 ad-hoc 签名，并在 README 里写清两种放行方式：
  1. 右键 → 打开 → 再点「打开」；
  2. `xattr -dr com.apple.quarantine /Applications/ChemFigStudio.app`
* **理由**：这是给个人/教学用的小工具，不是对外分发的商业软件；证书成本远高于收益。
  另外 `make bundle-lite`（不内嵌 Pillow，约 400 KB）+ 系统 `python3` 的路线也能规避"未知开发者"弹窗
  （脚本形式不触发 Gatekeeper）。
* **后果**：待办清单不再把"通用二进制 + 签名"列为待办；若将来真要对外分发，再补证书与公证流程。

---

## ADR-011：插件机制用"受信任的本地代码"，不引入沙箱

* **状态**：已接受（2026-10-16）
* **背景**：契约要求"人能自己增加/配置图类型"。可选做法：
  (a) 只支持声明式 JSON（安全但表达力弱）；
  (b) 支持 Python 插件（表达力强，但**加载即执行**）；
  (c) 支持 Python 插件 + 沙箱/子进程隔离（复杂，且 Python 3.9 无便宜可靠的沙箱）。
* **决定**：**(b)**。`plugins/*.py` 里的 `register(api)` 在启动时被加载执行，门面
  `PluginAPI` 只暴露稳定工具（`add_figure` / 参数便捷式 / `surface` / `core` / `figures` / `t`）。
  同时提供声明式 JSON 预置（`*.figure.json`，给内置图存一套常用参数）。
* **理由**：这是用户自己机器上的本地工具，插件来源与用户自己的脚本同级；沙箱带来的复杂度
  远超收益。README 明确写"只放自己写的/信得过的文件"。
* **配套保护**（都有测试）：
  * **内置图类型不可覆盖**（拒绝并报错，绝不悄悄改变既有输出）
  * **坏插件不拖垮程序**：逐文件记录 `error`，其余插件与内置功能照常
  * `CHEMFIG_PLUGINS` 可显式指定目录或 `off` 完全关闭（测试默认 off，保证隔离）
  * 插件图类型排在参数表**末尾**，不挤掉内置图的固定顺序

---

## ADR-012：3D 用 RDKit（可选内嵌）+ 自托管 3Dmol.js，不自己写 3D

* **状态**：已接受（2026-10-16）
* **背景**：需求是"给 chemfigstudio 加 3D"，并明确要求**不要造轮子**。
  3D 分子功能有两个必需部件，各自都有成熟实现：
  (a) **3D 构象坐标** —— 从 SMILES 得到真实三维几何（距离几何 + 力场优化）；
  (b) **3D 渲染** —— 球棍/空间填充、旋转缩放、深度缓冲。
  自研 (a) 要写距离几何与力场，自研 (b) 要写投影矩阵、深度排序与光照。
* **决定**：
  1. **构象用 RDKit**（`AllChem.EmbedMultipleConfs` + ETKDGv3，再 MMFF94 优化取最低能量），
     本项目只做**薄封装**（固定随机种子 → 同一分子每次同一构象；友好报错；只吐 molblock）。
  2. **渲染用 3Dmol.js**（BSD-3-Clause，`3Dmol-min.js` 538 KB）**放进仓库自托管**
     （`gui/static/vendor/`），**不用 CDN** —— 离线仍是硬要求。
  3. 服务端只在 `POST /api/render3d` 回**几何数据**（V2000 molblock），
     渲染发生在浏览器；`core.py`/`canvas.py` 一行不改（它们仍是 2D）。
* **理由**：
  * 构象生成的质量直接决定"3D 有没有用"，自己写的距离几何在教学场景都会出错；
    RDKit 是这一领域的参考实现，且本项目**早已**支持它作为可选依赖（ADR-006）。
  * 3Dmol.js 是 BSD 许可的成熟库，538 KB 可接受；换成 three.js 则要自己写分子语义
    （球棍比例、CPK 配色、元素标签），那正是"造轮子"。
  * 自托管而非 CDN：CDN 会让离线/受管网络下的界面直接白屏。
* **后果（诚实清单）**：
  * **体积**：RDKit + numpy 解包约 99 MB（裁掉 Contrib/sping/MolStandardize/测试数据后），
    内嵌版 app 由 ~13 MB 涨到 **113 MB**。`--no-vendor` 或只跑网页版可避免。
  * **架构绑定**：内嵌的 `rdkit`/`numpy` 是编译扩展，与 Python 版本、CPU 架构绑定；
    universal 二进制在另一种 CPU 的 Mac 上 3D 不可用（2D 出图不受影响）。
  * **没有 SVG**：3Dmol 只能出栅格帧（`canvas.toDataURL`），3D 静帧**只有 PNG**，
    这与 ADR-004 已改造过的 2D 双后端无关。
  * **不出图就不落盘**：3D 功能不写用户输出目录；导出是浏览器下载。
  * **RDKit 缺失时**：CLI 退出码 3、HTTP 503（环境能力缺失，不是 400 参数错误），
    并附可执行的安装命令。
  * 第三方许可随库附带（`gui/static/vendor/3Dmol-LICENSE.txt`）。
* **备选**：three.js/自写 WebGL（要自造分子语义 → 被否）；Mol*（unpacked 84 MB，对本项目过重）；
  py3Dmol/静态图像服务端渲染（失去交互，且仍要浏览器）。

---

## ADR-013：本地服务的写盘边界与请求来源校验（默认最小权限）

* **状态**：已接受（2026-10-16，回应外部审计 security-01/02/03）
* **背景**：GUI 是"只监听回环的本地服务"，长期被当作"本机 = 可信"。审计复现了后果：
  `/api/export` 与 `/api/batch` 把请求里的 `dir` 直接当写盘根，于是**用户浏览器里任何
  一个网页**（或 DNS rebinding 后的域名）都能发跨站 POST，让这个服务把文件写到文件系统
  任意位置、改设置、读本地 CSV。回环 ≠ 可信：浏览器会代第三方页面发起这些请求。
* **决定**：
  1. **写盘最小权限**：GUI/批量的写盘根 = `outdir` + 设置里的 `allowedWriteDirs`
     （默认空）。越界返回 **403** 并提示怎么加白名单。CLI 不受限制 —— 那是用户在终端里
     自己的动作，不是第三方页面的。
  2. **改写请求三道校验**：`Origin`（若存在）必须同源且主机为回环、`Host` 必须是回环
     （挡 DNS rebinding）、`Content-Type` 必须是 `application/json`
     （跨站"简单请求"只能发 text/plain 或表单，带不了 JSON）。
  3. **只拦写操作**：GET（静态资源、`/api/health`）不做来源校验，保持浏览器/原生外壳正常。
* **理由**：这三条是本地 HTTP 服务的最小成本防护，不引入任何依赖；白名单是**用户显式
  动作**（设置面板里的多行文本框），所以"导出到桌面/移动硬盘"这个真实需求仍然满足。
* **后果（诚实清单）**：
  * 服务仍然**没有认证**：同一台机器上以另一用户身份运行的进程、或已在本机执行代码的
    恶意程序，依旧可以调用这些接口。要更强需要令牌 / Unix socket，本项目不做
    （与 ADR-010 的同一取舍思路）。
  * `--host 0.0.0.0` 仍能把服务暴露到局域网；Origin/Host 校验挡得住跨站页面，
    挡不住局域网内的非浏览器客户端。README 明说这是本地工具。
  * 白名单目录一旦加入，该目录内的写入就回到"信任用户"状态 —— 不要加 `/`。
* **备选**：只在客户端过滤（无效，攻击者直接发请求）；令牌（对本地工具过重，且原生
  外壳与浏览器都要跟着改）；Unix socket（跨浏览器不可行）。

---

## ADR-014：取代 ADR-004 —— 现在输出 PNG / SVG / PDF 三种格式

* **状态**：已接受（2026-10-16），**取代 ADR-004**
* **背景**：ADR-004 决定"v0.2 只出 PNG"，并写明"需要 SVG 时先写一条 ADR 再动代码"。
  这条 ADR 一直没补，于是文档与代码长期打架：`settings.VALID_FORMATS` 是
  `("png", "svg", "pdf")`，`canvas.Surface` 已经是**双后端**（同一份绘制指令同时出位图
  与矢量），`/api/export` 与 CLI `--format svg|pdf` 都能用，测试也覆盖了 —— 但
  `ARCHITECTURE.md §11`、`§7.4`、ADR-004 与 README 仍按老说法写"只支持位图"。
* **决定**：以"PNG / SVG / PDF 三格式"为现状，把过时表述改成事实：
  1. **SVG 是真矢量文本**：`canvas.Surface(svg=True)` 把每个图元同时写成 SVG 元素，
     文字是 `<text>`（可选中、可搜索），不是把位图描成路径。
  2. **PNG / PDF 走位图后端**（Pillow），PDF 由 Pillow 的 `save(..., "PDF")` 产出。
  3. **三种格式共用同一取景**：字体度量取自 Pillow 的 `textlength`，裁边用同一套墨迹
     包围盒，所以图元位置偏差 < 1%（有测试）。
  4. **"300 dpi" = 重新绘制成更大的图**（`Surface(scale=…)`），不是把位图插值放大。
* **理由**：报告用 Word/PDF，插图要能放大不糊；而"文字可选中"是老师/同学复制化学式的
  实际需求。实现成本已经付过（一次后端抽象，见 `canvas.py`），没有理由继续声称不支持。
* **后果**：
  * 3D 静帧**没有**矢量输出（3Dmol 只能 `canvas.toDataURL()`，见 ADR-012）——
    本 ADR 的三格式只覆盖 2D 图。
  * 新增图元类型时**必须同时在两个后端实现**，否则 SVG 会缺元素；`tests/test_export.py`
    对三种格式都有断言。
  * 插件的 `api.surface(...)` 拿到的也是同一个双后端 Surface，所以插件自动获得三格式。
  * 文档侧加了守卫（`tests/test_export.FormatDocumentationTests`）：现役文档不得再出现
    旧的"只支持位图"说法，ADR 原文与 CHANGELOG 历史段豁免。
* **备选**：继续只出 PNG（已被实际需求否掉）；用 `svgwrite`/`cairosvg` 另写一条通路
  （会引入新依赖，且两套坐标要各自维护，容易漂移）。

## ADR-015：结构文件**双向**——新增 molblock / SMILES / InChI 导出（2026-10-17）

**背景**：ADR-012 之后工具能**读**真实分子（SMILES/molblock/SDF，见 `structure.py`），
但只能读、不能写。这意味着画出来的东西**出不去**：没法交给 ChemDraw、没法当
PubChem/Avogadro 的输入、没法进数据库查重。"结构文件进出"缺的是后一半。

**决策**：新增三个导出目标，共用一条 `Skeleton → molblock` 的核心路径：

| 目标 | 依赖 | 用途 |
|---|---|---|
| **molblock / SDF（V2000）** | **零依赖**（纯文本拼装） | 交给任何化学软件 |
| **规范 SMILES** | RDKit | 当输入、贴到别处 |
| **InChI** | RDKit | 唯一标识符、数据库查重 |

CLI 入口是 `chemfigstudio convert`，格式按输出扩展名推断
（`.mol`/`.sdf` → molblock，`.smi` → SMILES，`.inchi` → InChI）。

**理由**：
* molblock 是事实标准（几乎所有化学软件都读），且能纯文本生成 —— 不该因为它
  "需要 RDKit" 才提供，零依赖那条路必须自己走通。
* SMILES/InChI 的规范化（canonicalization）是**算法问题**，不是画图问题；
  自己实现等于重造轮子且几乎一定错，所以这两个明确依赖 RDKit（可选）。
* InChI 是"这个分子是不是同一个"的答案，查重用得着；SMILES 是"怎么把它写出来"。

**后果（诚实清单）**：
* **不写显式氢**：我们的模型里氢是隐含的（这是结构式的本意）。molblock 只有重原子，
  读入方按价键补氢 —— 合法且体积小，但**氢的坐标不存在**，所以不是 3D 结构。
* **立体化学是"尽力而为"**：楔形写成 molblock 的 bond stereo（1=up，6=down），
  依赖读入方按坐标+楔形推断手性。我们自己的 `known_stereo` 不写进文件（molblock
  没有这个字段）。
* **只写 V2000**：超过 999 个原子要 V3000，本项目不做（画图工具的分子没这么大）。
* **芳香环写 Kekulé**：不写 RDKit 的芳香键类型（molblock V2000 也不直接支持）。
  RDKit 读入时会 sanitize 回芳香性，所以 InChI 是对的。
* **InChI 依赖 RDKit**：没装 RDKit 时 `to_inchi` 抛 `ConfigError`（可执行的安装提示），
  而 `to_molblock` 仍然可用。
* **InChI 有版本**：RDKit 默认出 1.06 版标准 InChI；比较时要注意版本一致。

**备选**：
* 手写 SMILES 生成器 —— 否决：规范化是算法问题，且芳香性/手性/电荷的表示
  极易出错，错的方式还很隐蔽。
* 只做 molblock 不做 InChI —— 否决：InChI 是查重场景的唯一答案，成本只有一行。
* 用 `openbabel` —— 否决：新依赖（约 100 MB）且 RDKit 已在，功能重叠。

---
