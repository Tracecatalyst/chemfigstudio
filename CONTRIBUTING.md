# 贡献指南（CONTRIBUTING）

> 日常闸门、修 bug 的循环、发布流程都在 [`docs/WORKFLOW.md`](docs/WORKFLOW.md)。


> 面向人类贡献者。

## 环境

不需要安装任何东西：`PYTHONPATH=src` 即可运行。可选 `pip install -e .` 后使用 `chemfigstudio` 命令。

```bash
cd chemfigstudio
make doctor     # 自检
make test       # 303 个测试，约 19 秒
```

## 改动流程

1. **先写测试**（`tests/`）。这个项目的所有行为都能用 unittest 表达；
   GUI 也一样（真起 HTTP 服务，用 `urllib` 打接口）。
2. 改实现（`src/`）。遵守 [`ARCHITECTURE.md §6`](ARCHITECTURE.md) 的不变量。
3. `make test` 全绿 + `make lint` 通过。
4. `make figures` 生成示例图，肉眼确认风格没变（尤其是**没有空心方块 □**）。
5. 更新 `CHANGELOG.md` 的 `Unreleased` 段。
   改了 `native/macos/` 的话跑一次 `make bundle`（会编译 Swift 并校验 plist）。
6. 提交（见下）。

## 提交信息

Conventional Commits，英文，一行 ≤ 72 字符，正文说明**为什么**：

```
feat(cli): 增加 batch 子命令，支持从 CSV 批量出图
fix(core): 用 with 打开图片，消除 ResourceWarning
docs(adr): 新增 ADR-008 说明 SVG 后端的取舍
test(gui): 覆盖 /api/render 的 500 分支
```

类型：`feat` `fix` `docs` `test` `refactor` `chore` `perf`。

## 代码风格

* Python 3.9 兼容；文件头 `from __future__ import annotations`。
* 标识符/docstring 英文，面向用户的文案中文。
* 库代码不 `print` / 不 `sys.exit`；结果用返回值或异常表达。
* 依赖单向：`gui/cli → api → figures → core`。
* 新增第三方依赖需先写 ADR，默认答案是"不加"。

## 报告问题

请附上：`make doctor` 输出、完整命令、期望结果、实际结果（有图更好）、
以及 `--json` 或 GUI 返回的 `warnings`。图被裁切、出现 `□`、文字重叠都属于 bug。

## 想加一种新图？

见 [`ARCHITECTURE.md §7.2`](ARCHITECTURE.md)：写 `figures.xxx_figure()` →
在 `api.py` 注册 `FigureSpec` → 加测试。前端与 `/api/figures` 会自动适配。

## 评审清单

- [ ] 测试覆盖新行为；`make test`、`make lint` 全绿
- [ ] 未违反硬约束（Unicode 上下标、`kJ/g` 写法、旧 CLI 兼容、依赖方向）
- [ ] 新参数出现在 `api.py` 的 `ParamSpec` 里（GUI 才会自动出现）
- [ ] `CHANGELOG.md` 已更新；接口/架构变化时 `ARCHITECTURE.md` 同步
- [ ] `examples/out/` 的图风格正确
