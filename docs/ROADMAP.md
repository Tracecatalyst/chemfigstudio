# ROADMAP —— 还差什么、做到哪了

> 这份文件回答一个问题：**这个工具离"专业可用"还差什么**。
> 每一项都带**证据**（commit / 代码位置 / 核实命令），不写"大概做了"。
>
> 背景：本工具的主场景是"参数 → 课本风格结构式"。专业工具（ChemDraw / Marvin /
> ChemDoodle）的主场景是"结构 → 一切"。两者的差距是**结构文件进出 + 立体化学 +
> 价键模型 + 性质计算 + 互操作标准**。这份清单就是按这个差距排的。

---

## 分级

| 级 | 含义 | 判断标准 |
|---|---|---|
| **金** | 补上它，工具才算**能读真实分子** | 别人给你一个 `.mol` 或 SMILES，你能打开并画出来 |
| **银** | 补上它，工具才算**专业可用** | 画出来的东西化学上站得住、能和别的软件交换文件 |
| **铜** | 体验/打磨 | 不影响正确性，只影响顺手程度 |

---

## 已完成（有 commit 可查）

| # | 事项 | 级 | 证据 |
|---|---|---|---|
| ① | **导入 molblock / SDF / SMILES** → 可编辑骨架 | 金 | `CHANGELOG.md` · `structure.py::parse_skeleton_input` |
| ② | **楔形键**（实心/虚线，窄端=立体中心） | 金 | `CHANGELOG.md` · `skeleton.WEDGE_UP` |
| ⑤ | **3D 构象查看器**（RDKit + 自托管 3Dmol.js） | 银 | ADR-012 · `render3d.py` · `/viewer3d` |
| ⑥ | **2D/3D 对照页**（多分子并排，接进 app） | 银 | `CHANGELOG.md` · `/compare` |
| — | 杂原子标签按**价键环境**推断（咖啡因的 N 不再写成 `NH₂`） | 银 | `CHANGELOG.md` |
| — | 默认就能找到 RDKit（搜索深度 4→8） | 金 | `CHANGELOG.md` |
| ③ | **价键检查**（超价报警，欠价不报、电荷修正、未知元素跳过） | 金 | `skeleton.py::Skeleton.valence_errors` · `tests/test_skeleton.py::ValenceTests` |
| ④ | **结构导出**：molblock（零依赖）/ 规范 SMILES / InChI | 银 | ADR-015 · `structure.py::to_molblock` · `cli.py::_cmd_convert` |
| — | **芳香环圆圈**（`rings` 参数，默认关；拓扑启发式） | 银 | `skeleton.py::Skeleton.aromatic_rings` · `figures.py::_draw_aromatic_circles` |
| — | **标签排版**（有标签不画圆点，键缩到文字边缘） | 铜 | `figures.py::_label_extent` · `figures.py::_shrink_to_label` |

核实命令：

```bash
PYTHONPATH=src python3 -c "from chemfigstudio.skeleton import parse_skeleton_input, WEDGE_UP; print('①②ok')"
PYTHONPATH=src python3 -c "from chemfigstudio.render3d import embed3d; print(embed3d('CCO').formula)"
ls src/chemfigstudio/gui/static/{viewer3d,compare}.js
```

---

## 待办（按建议顺序，逐项勾掉）

### 金 —— 让"读进来的分子"不出错

### 银 —— 让"画出来的分子"能交换

### 铜 —— 打磨

- [x] **对照页导出**：把某一行的 2D+3D 合成一张 PNG 存下来（`2a22107`）
- [ ] **3D 导出 SVG**：3Dmol 只能出栅格（`pngURI`），要做 SVG 得换渲染器 —— 大概率不做
- [x] **对照页加进 `make browser-check`**：3 条断言（24 条总计）（`2a22107`）

---

## 明确**不做**（避免误期待）

| 不做 | 原因 |
|---|---|
| 反应式配平 / 反应预测 | 那是化学信息学平台的事，不是画图工具 |
| 光谱模拟（IR/NMR/质谱） | 同上，且需要量子化学后端 |
| 分子对接 / 构象搜索（多构象） | RDKit `EmbedMultipleConfs` 只做"取最低能量"，不做搜索 |
| 3D 编辑（拖原子改构象） | 3D 只出图；要编辑请用 Avogadro/PyMOL |
| 读别人的 3D 场景（`.sdf` 当 3D） | 3D 是"只出不进" |
| 第二个界面实现 | 违反 ADR/ARCHITECTURE §6：界面只有 `gui/static/` 一份 |

---

## 怎么更新这份清单

1. 做完了 → 把 `[ ]` 改 `[x]`，并在"已完成"表加一行**带 commit hash 的证据**
2. 发现新缺口 → 加进对应分级，写清"现状"（用命令核实过，不靠印象）
3. **不许**只改文字不改状态；状态要么有代码，要么有测试，要么标"未做"
