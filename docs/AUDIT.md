# AUDIT —— 给审计 agent 的入口

> 你是被叫来审计这个仓库的 agent。这份文件告诉你要读什么、**哪些已经查过了**、
> 怎么验证结论，以及报告该长什么样。
>
> 目标不是"找出很多问题"，而是**找出还没被发现的问题** + 给出可复现的证据。

## 1. 你在哪

```bash
cd <公开仓库>          # 例如 ~/Documents/chemfigstudio（git clone 下来的那个）
# 或
cd <私有开发库>        # 内部路径，向维护者要
```

两个仓库的代码基本一致（公开副本去掉了内部文档与构建测试）。**默认审公开副本**；
要审打包脚本、macOS 外壳这类只有私有库才有的东西，再去私有库。

当前基线：

| | 值 |
|---|---|
| 版本 | **0.5.0** |
| 测试 | **488**（公开） |
| 闸门 | `make lint` · `make typecheck` · `make test` · `make check-privacy` · `make browser-check` |

## 2. 十分钟上手（只读这三个）

| 顺序 | 文件 | 你会拿到什么 |
|---|---|---|
| 1 | `ARCHITECTURE.md` | 分层（`gui/cli → api → figures → core`）、**不变量 §6**、HTTP 契约 |
| 2 | `CONTRIBUTING.md`（私有库另有 AGENTS.md，更详细） | 硬约束、已知坑、改动工作流 |
| 3 | `docs/ROADMAP.md` | 已完成的（带证据）与**明确不做**的 —— 别把"设计如此"当成 bug |

想更快：`docs/STYLE_SPEC.md`（视觉规范）、`docs/DECISIONS.md`（ADR，含被否决的方案）。

## 3. 上一轮审计做了什么（**别重复**）

`audit/2026-10-16/` 有一次完整审计（44 个 agent，9 个视角）：5 HIGH / 28 MED / 54 LOW。
基线是 v0.3.0，现在已经是 v0.5.0。

### 5 个 HIGH 的现状

| ID | 问题 | 状态 |
|---|---|---|
| `security-01` | `/api/export`、`/api/batch` 把请求里的 `dir` 直接当写盘目录 | ✅ **已修**：`_resolve_write_dir` + `allowedWriteDirs` |
| `security-03` | 变更类接口没有 Host/Origin/Content-Type 校验（跨站 POST） | ✅ **已修**：`_check_request_origin` |
| `seams-01` | 批量预览的 fallback 是 `"figure"`，实际写入用 `kind`（默认 `alcohol`） | ✅ **已修**：共用一套 fallback，有回归测试 |
| `hygiene-01` | 同上（同一处根因） | ✅ **已修** |
| `uipromise-01` | 界面上的「在访达中显示」按钮 disabled 后从不恢复 | ❌ **还没修** —— 这是已知的开口 |

**先读 `audit/2026-10-16/99-FINDINGS.md`**，里面 MED/LOW 一共 82 条。
已修的会在 `CHANGELOG.md` 对应版本里；没在 changelog 里出现的，很可能还在。

### 第二轮（2026-10-18，基线 v0.5.0）—— 4 条新问题

| ID | 问题 | 状态 |
|---|---|---|
| `F1` | 碳标签的氢数只看连接度、不看键级与电荷（甲烷画成 `CH₃`、乙烯画成 `CH₃=CH₃`） | ✅ **已修** `4ff25d6`：氢数用 RDKit 的 `GetTotalNumHs`，molblock 用 `4 − Σ键级 − \|电荷\|`；31 个碳对 RDKit 零差异 |
| `F2` | `convert` 静默把不支持的化学换成另一个分子（Si→C、Na⁺→CH₃⁺、同位素/自由基丢失）并返回 `ok:true` | ✅ **已修** `4ff25d6`：不认识就 `RenderError`；逃生口 `allow_unknown_elements` 仍记录替换；决策见 ADR-016 |
| `F3` | 双键 E/Z 不存储、导出时从坐标反推（未指定的亚胺被断言成 (E)，拖原子就换异构体） | ✅ **已修** `635a831`：`Bond.stereo` 字段 + 导入抄 `GetStereo()` + 导出以字段为准；拖过原子时 `stereo_conflicts()` 警告 |
| `F4` | `core.py` 的化学守卫词表只有 10 个词、只扫 `str`，注入 `NH_{2}`/`C=O`/`b"OH"` 仍全绿 | ✅ **已修** `4ff25d6`：词表扩到 20 个、解码 `bytes`、加"注入即变红"自证测试 |

第二轮还带出一条**不在审计报告里**的化学问题：芳香环判定把 1,4-苯醌画成芳香
（教科书反例），并把呋喃/吡咯/噻吩判成非芳香。已改成 Hückel 规则
（`0a93324`），有 RDKit 时以它的 `GetIsAromatic` 为准。

**仍然开着的**：`uipromise-01`（第一轮，见上表）。

## 4. 查什么（按收益排序）

### 4.1 最值得查的三类

1. **"看起来对、化学错"** —— 这个项目历史上最严重的一类 bug。四条真实案例：
   - 羧基的两个氧都被写成 `OH`（应该一个 `O` 一个 `OH`）
   - 骨架碳全被标成 `CH₃`（结构式里碳应该是一个顶点，不写字）
   - 楔形窄端画在宽端（**那是另一种对映体**）
   - 咖啡因三个氮全写成 `NH₂`（它们是 N-甲基和环内 `=N-`，没有氢）

   查法：拿真实分子过一遍 `structure.from_smiles()` → 看图 → 对照化学常识。
   想快速扫一批：`make showcase`（12 个分子）和 `make compare`（2D/3D 对照）。

2. **不变量被违反** —— `ARCHITECTURE.md §6` 那几条不是建议：
   - 库代码不许 `print` / `sys.exit`（结果靠 `SaveReport.warnings` 或异常）
   - 依赖方向单向，不许反向 import
   - `src/chemfigstudio/core.py` 不许认识化学概念（有 AST 守卫，但守卫可能有漏洞）
   - 界面只有一份（`gui/static/`），不许有第二个实现

3. **测试是假绿** —— 这个项目栽过四次：
   - 漏移植 `_draw_hashed_wedge`，40 条测试全绿（没有一条走那个分支）
   - 注入"中间产物不清理"，测试仍绿（只查了猜的名字前缀）
   - 注入"去掉 `quiet_rdkit`"，测试仍绿（`redirect_stderr` 抓不到 fd 2）
   - 两条测试**从没跑过**（同名类静默覆盖）

   查法：找一条守卫测试，**故意把实现改坏，确认它变红**。不变红的就是假守卫。

### 4.2 已经查过、别再花时间的

- CSV 解析、i18n key 一致性、ADR 编号悬空、重复类名/方法名（都有守卫测试）
- 写盘边界、跨站请求（`security-01/03` 已修，有测试）
- 逐字节回归（6 张示例图，每次改渲染都要跑）

### 4.3 已知盲区（有价值的审计目标）

| 盲区 | 说明 |
|---|---|
| **截图里的信息** | `docs/images/gui-*.png` 曾经泄露完整文件路径，脚本**读不出像素里的字**，只能人工看 |
| **化学正确性** | 没有任何自动化能判断"这张图化学上对不对"，只能看图 |
| **跨平台** | 只在 macOS 上跑过。Windows 有启动脚本和配置目录分支，**从没在真 Windows 上验证** |
| **3D 的架构绑定** | 内嵌的 `rdkit` 是编译扩展，只对当前 Python 版本 + CPU 架构有效 |
| **`uipromise-01`** | 已知未修 |

## 5. 怎么验证结论（**不许只靠读代码**）

每条发现都要能复现：

```bash
# 1) 基线必须绿（不绿先别审计，环境有问题）
make lint && make typecheck && make test

# 2) 跑你怀疑的那条路径
PYTHONPATH=src python3 -m chemfigstudio.cli <子命令> ...

# 3) GUI 相关：起真服务，用 curl 或浏览器打
PYTHONPATH=src python3 -m chemfigstudio.gui.server --port 8899 --outdir /tmp/o --no-browser &
curl -s -X POST http://127.0.0.1:8899/api/... -H 'Content-Type: application/json' -d '{...}'

# 4) 前端 JS 相关：真浏览器（ego-browser 在 ~/.local/bin）
export PATH="$HOME/.local/bin:$PATH"
make browser-check        # 24 条断言

# 5) 渲染相关：改完必须逐字节比对 6 张示例图
make figures
tmp=$(mktemp -d); git archive HEAD examples/out | tar -x -C "$tmp"
for f in examples/out/*.png; do
  cmp -s "$tmp/examples/out/$(basename "$f")" "$f" && echo "✅ $(basename "$f")" || echo "❌ $(basename "$f")"
done; rm -rf "$tmp"
```

**纪律**：发现 → 复现 → 修 → **注入验证**（把修复回退，确认测试变红）→ 恢复 → 跑全闸门。

## 6. 报告格式

每条发现给这五项，缺一项就不算发现：

```markdown
### <id> — <severity: blocker|high|medium|low>
**位置**：`src/chemfigstudio/xxx.py:123`
**问题**：一句话说清"发生了什么"，不要复述代码
**复现**：能直接粘贴运行的命令 / 最小输入
**证据**：实际输出（错误信息、图、测试结果）
**建议**：怎么改；如果涉及化学，说明正确的化学是什么
```

按 severity 排序。**低置信度的单独列一节**，别混进确定的问题里 —— 一个被证伪的
HIGH 比漏掉一个 MED 更损害信任。

## 7. 不要做的事

| 不要 | 原因 |
|---|---|
| 装新依赖 / 改 `pyproject.toml` | 新增依赖必须先写 ADR，默认答案是"不加" |
| `git push` / 改写历史 / `force push` | 仓库已发布，历史改写是破坏性操作，必须由人决定 |
| 改 `audit/` 里的历史结论 | 那是留档 |
| 为了"通过"而放宽检查 | 尤其：别删 `tests/test_repo_health.py` 的守卫、别调宽 `scripts/check-privacy.sh` 的正则 |
| 只读代码就下结论 | 这个项目的 bug 大多要**跑起来看图**才暴露 |
| 把"设计如此"报成 bug | 先看 `docs/ROADMAP.md` 的「明确不做」和 `docs/DECISIONS.md` 的 ADR |

## 8. 一句话

**找还没被发现的问题，给能复现的证据，别把已知的事情再说一遍。**
