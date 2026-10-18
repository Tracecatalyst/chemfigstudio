# WORKFLOW —— 这套流程怎么跑

> 一句话：**闸门用命令跑，别靠记。** 下面每条都有对应的 `make` 目标或脚本。

## 日常

```bash
make check        # lint + test + 隐私自检         ~25 s，无外部依赖
make check-all    # 加 typecheck + 示例图逐字节 + 真浏览器   ~2 min
```

`make check` 是提交前的最低要求。`check-all` 在改渲染、改前端、发版前跑。

## 改一个 bug

1. **先复现**。写出能让它出错的命令或输入，贴进 commit message。
   不能复现的 bug 不修 —— 修的是猜测。
2. **修**。
3. **注入验证**：把修复改坏，确认测试变红；恢复，确认变绿。
   不会变红的测试等于没测（这个仓库栽过四次）。
4. `make check`。
5. 如果碰了渲染：`make figures-verify` —— 它重新生成示例图并问 git 有没有变化。
   变了就必须解释是哪条规则变了，并提交新基线。

## 加一个功能

- 改行为 → 先加/改测试。
- 新的**依赖**或**架构决策** → 先写 ADR（`docs/DECISIONS.md`），默认答案是"不加"。
- 新的用户可见行为 → `CHANGELOG.md` 的 `## [Unreleased]` 加一条。
- 界面改动 → `make browser-check`（真浏览器，24 条断言）。

## 发布

```bash
# 1. 先把 CHANGELOG 的 Unreleased 滚成 '## [x.y.z] - YYYY-MM-DD'
# 2. 写 dist/release-notes-vX.Y.Z.md（15 行以内，别写成报告）
# 3. 一条命令：

scripts/release.sh 0.5.2            # 真发布
scripts/release.sh 0.5.2 --dry-run  # 只检查
```

它会：查工作区干净 / 查 tag 不存在 / 改 4 处版本号 / 要求 CHANGELOG 段已存在 /
跑 `make check` / 提交 + tag + 推送 / 构建 .app / 建 release + 上传 / 比对 sha256。

**它不替你写变更内容** —— 那是唯一必须人做的事。

## 四个"必须"

| 必须 | 为什么 |
|---|---|
| 复现再修 | 这个项目的 bug 大多要跑起来看图才暴露 |
| 注入验证 | 不会变红的测试是假绿，比没有更糟 |
| 改渲染跑 `figures-verify` | 6 张示例图是逐字节基线 |
| 发布跑 `release.sh` | 手工做过两次，两次都漏步骤 |

## 已知的坑（都踩过）

- **测试别依赖持久配置**。`tests/_bootstrap.py` 把 `CHEMFIG_CONFIG_DIR` 指到 `dist/test-cfg`，
  它跨运行保留。写配置的测试类必须用自己的临时目录（`CliTestCase` / `GuiHttpTests` 都这么做了）。
- **界面是数据驱动的**。参数表单由 `/api/figures` 的响应渲染，`grep index.html` 找控件没有意义。
- **3D 图不用截图**。`Page.captureScreenshot` 在这个页面上必超时；用 3Dmol 的 `pngURI()`
  （`scripts/make-3d-images.js`）。
- **截图人工看**。`make check-privacy` 读不出像素里的字，它会提醒你"有截图，看一眼"。
- **bash 里中文紧跟变量要加花括号**：`${VERSION}（` 而不是 `$VERSION（` —— 全角括号不是分隔符。
