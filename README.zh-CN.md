# chemfigstudio

**在本地生成缩合式（示性式）化学插图。** 不联网、不用浏览器引擎、不依赖第三方绘图软件 ——
只有一个小 Python 包和 Pillow。

![chemfigstudio 界面](docs/images/gui-light.png)

*本地网页界面（1440×900）。另有深色模式：`docs/images/gui-dark.png`。*

**仓库地址：**https://github.com/Tracecatalyst/chemfigstudio


---

## 能画什么

七种图，都能用一条命令或一次 API 调用复现：

| 图类型 | 内容 | 预览 |
|---|---|---|
| `alcohol` | 单个直链醇：示性式、名称、化学式、文献 ΔHc | ![alcohol](docs/images/figure-alcohol.png) |
| `series` | 多种醇逐行对照，用于"碳链越长，每克放热越多"的推理 | ![series](docs/images/figure-series.png) |
| `reaction` | 完全燃烧反应，**按碳数自动配平**并标注 ΔH | ![reaction](docs/images/figure-reaction.png) |
| `chcount` | 每个转角标出 C–H 数目（3 C–H / 2 C–H …） | ![chcount](docs/images/figure-chcount.png) |
| `enthalpy` | 班级测定值 vs 文献值条形图，含偏差百分数 | ![enthalpy](docs/images/figure-enthalpy.png) |
| `esterification` | 羧酸 + 醇 → 酯 + 水 | ![esterification](docs/images/figure-esterification.png) |
| `skeleton` | 可编辑的原子/键骨架（环、支链、N/S/Cl/Br/P） | ![skeleton](docs/images/figure-skeleton.png) |

## 为什么做它

- **全本地**：不联网，考场里也能用。
- **可复现**：同样输入 → **逐字节相同**的 PNG。测试会锁定哈希，所以重构不会偷偷改掉你已经发表的图。
- **矢量输出**：`--format svg` 产出真正带 `<text>` 的 SVG（可搜索、可选中、可放大），而不是把字转成路径。
- **直链醇不需要化学工具包**；若装了 RDKit，会自动用于支链/环/特殊 SMILES。
- **中英双语**界面与报错，且有键值一致性测试。

## 安装

两种方式 —— 看你想要**用**它还是**改**它。

### 只想用（macOS 上不需要装 Python）

从 [最新 Release](../../releases/latest) 下载 `ChemFigStudio.app`，解压，双击。
它内嵌了 Python、Pillow 和 RDKit，不用另装任何东西。第一次在别人电脑上打开需要
右键 → 打开（或 `xattr -dr com.apple.quarantine ChemFigStudio.app`），
因为签名只是 ad-hoc。

### 想改（任何平台）

需要 Python 3.9+ 和 Pillow，不往系统里装东西：

```bash
git clone https://github.com/Tracecatalyst/chemfigstudio.git && cd chemfigstudio
PIP_USER=1 python3 -m pip install --user pillow      # 还没装 Pillow 的话
make test                                            # 472 项测试，离线可跑
```

RDKit 是**可选**的：没有它也有全部七种图、骨架编辑、molblock/SDF 进出；
装了它还能用任意 SMILES 与 3D 构象（`python3 -m pip install --target .pylibs rdkit numpy`）。

当前版本：**0.5.0**（`python3 -m chemfigstudio.cli --version`）。

## 快速上手

### 一、网页界面

```bash
make gui            # 启动 http://127.0.0.1:8765 并自动打开
```

三栏布局：左边**参数**、中间**画布**、右边**图层 / 输出**。改参数立即刷新预览；
`Generate` 写出文件；`Show in Finder` 定位文件。

每个文档是一个**标签页**：`New tab` 新建、点标签切换（参数与预览会跟着换）、
`Close tab` 关闭、**双击标签**改名；每个标签各有 100 步撤销/重做。

### 二、命令行

```bash
PYTHONPATH=src python3 -m chemfigstudio.cli alcohol --n 4 --name butan-1-ol --dh -36.1
PYTHONPATH=src python3 -m chemfigstudio.cli series --include-methanol -o series.png
PYTHONPATH=src python3 -m chemfigstudio.cli reaction --n 4 --dh-mol -2670 --dpi 300
PYTHONPATH=src python3 -m chemfigstudio.cli doctor
```

每个子命令都支持 `--format png,svg`、`--dpi`、`--transparent`、`--json`。

### 三、Python API

```python
import sys; sys.path.insert(0, "src")
from chemfigstudio import api

result = api.render("reaction", out="combustion.png",
                    params={"n_carbons": 4, "delta_h_kj_per_mol": -2670},
                    formats=("png", "svg"), dpi=300)
print(result.size, result.warnings)
```

## 编辑骨架

`Edit skeleton…` 打开原子/键编辑器：点击加碳、点键在单键→双键→三键间循环、
从调色板选元素、放置官能团，`Apply` 后走同一套渲染器。`skeleton` 也接受 JSON，
所以编辑器里的一次操作可以用脚本复现。

## 结构文件进出

`skeleton` 字段接受**三种输入**，按内容自动判断：

| 输入 | 例子 | 依赖 |
|---|---|---|
| **SMILES** | `c1ccccc1O` | RDKit（出 2D 坐标与立体化学） |
| **molblock / SDF**（V2000） | ChemDraw / PubChem 导出的 `.mol` | **零依赖**，纯文本解析 |
| 骨架 JSON | 编辑器自己的格式 | 无 |

导入后就是普通可编辑骨架：能拖原子、改键级、直接出图。支持立体键，**窄端永远在立体中心**
（画反了就是另一种对映体）。

反过来也能导出：

```bash
chemfigstudio convert --skeleton "CCO" -o ethanol.mol     # molblock（零依赖）
chemfigstudio convert --skeleton "CCO" -o ethanol.smi     # 规范 SMILES（需 RDKit）
chemfigstudio convert --skeleton "CCO" -o ethanol.inchi   # InChI（需 RDKit）
```

格式按输出扩展名推断。两条诚实限制：导出的 molblock **不写显式氢**（结构式里氢是隐含的，
所以也没有氢的坐标 —— 它不是 3D 结构），且只写 **V2000**。

**价键检查**：导入与出图时会报出超价的原子（比如碳连了 5 根键）。判据刻意保守 ——
按键级之和算、**不报欠价**（氢是隐含的）、电荷会调整上限、表里没有的元素一律跳过。

## 2D vs 3D 对照

`/compare` 把同一分子的结构式与真实 3D 构象并排放，可以一次看多个，右边能拖动旋转。
每一行都能导出成一张包含两半的 PNG。

3D 那半是 WebGL 画布，所以导出在浏览器里合成（服务端没有它的像素）。

## 批量

`Batch…` 读取 CSV（`kind,参数,…`）逐行成图，**逐行报错而不整体中断**，适合一整套醇。

## 导出

`Export image…` 输出 PNG / SVG / PDF，可选透明背景与 DPI；若目标文件已存在，
会先要求确认，不静默覆盖。

## 插件

把 Python 模块放进插件目录即可注册新的图类型（`chemfigstudio_plugin_entry`）。
`CHEMFIG_PLUGINS=off` 关闭加载；`plugins/` 里有可运行的示例。

## macOS 应用（可选）

```bash
make app                 # dist/ChemFigStudio.app（当前架构）
make bundle-universal    # Intel + Apple Silicon 通用包
```

它是一个很薄的 Swift/WKWebView 外壳，包着同一个本地服务。**未签名**（没有开发者证书），
首次打开需要走一次 Gatekeeper 例外。

## 开发与验收

```bash
make lint            # 语法检查（ast.parse）
make typecheck       # mypy
make test            # 264 项单测（离线）
```

规矩：每个修复都在同一次提交里带上测试；渲染重构要用**逐字节比对**证明输出没变。

## 已知限制

- 内置 SMILES 解析器只认直链伯醇；其他结构请装 RDKit。
- `series` 的行默认固定为文献五元组，可用 `--include-methanol` 或 `--entries` 覆盖。
- macOS 应用未签名。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
