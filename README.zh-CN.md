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

需要 Python 3.9+ 与 Pillow，不装任何系统级依赖：

```bash
git clone https://github.com/Tracecatalyst/chemfigstudio.git && cd chemfigstudio
PIP_USER=1 python3 -m pip install --user pillow      # 若还没装 Pillow
make test                                            # 278 项测试，离线可跑
```

## 快速上手

### 一、网页界面

```bash
make gui            # 启动 http://127.0.0.1:8756 并自动打开
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
PYTHONPATH=src python3 -m chemfigstudio.cli list
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
make lint            # ruff
make typecheck       # mypy
make test            # 278 项单测（离线）
make browser-check   # 真浏览器里的 21 项端到端检查
make ui-shots        # 多宽度布局断言 + 截图
```

规矩：每个修复都在同一次提交里带上测试；渲染重构要用**逐字节比对**证明输出没变。

## 已知限制

- 内置 SMILES 解析器只认直链伯醇；其他结构请装 RDKit。
- `series` 的行默认固定为文献五元组，可用 `--include-methanol` 或 `--entries` 覆盖。
- macOS 应用未签名。

## 许可

MIT —— 见 [LICENSE](LICENSE)。
