# 界面截图 —— 拍什么、怎么拍

README 里原本有两张界面截图，来自初版。它们被删掉了，因为左栏底部多了 6 个按钮
（Export / Copy / Batch / Edit skeleton / 3D molecule viewer / 2D vs 3D），旧图一个都没拍到。

> **一条踩过的坑**：最初删图的理由是"图里有个 `label_style` 控件，当前界面已经没有了"。
> 那是**错的** —— 这个界面是**数据驱动**的，参数表单由 `/api/figures` 的响应动态渲染，
> 所以 `index.html` 里 grep 不到 `label_style`，但控件一直在（`/api/figures` 里明确返回
> 它）。**静态 grep 不能回答"界面还有没有某个控件"。**

## 为什么不能自动拍

无头浏览器在这个页面上截不了图 —— 五种方式全部超时（Playwright、CDP
`Page.captureScreenshot`、`fromSurface:false`、注入 CSS 冻结动画、隐藏 canvas）。
`scripts/browser-check.js` 之所以能跑，是因为它**只用 DOM 断言，从不截图**。

结论：界面截图只能人工拍。

## 需要三张

| 文件 | 页面 | 状态 |
|---|---|---|
| `docs/images/gui-dark.png` | `http://127.0.0.1:8765/` | ✅ 已有（v0.5.1，深色，路径已抹） |
| `docs/images/gui-light.png` | 同上 | ⬜ 待补（亮色主题） |
| `docs/images/gui-light.png` | 同上（亮色） | ⬜ 待补 |
| ~~`docs/images/compare.png`~~ | — | ✅ **已用 `pngURI()` 生成，不需要截图** |

## 3D 图不需要截图

3Dmol 的 `pngURI()` 就是 `canvas.toDataURL()` —— 在 JS 层直接拿像素，绕开
`Page.captureScreenshot`（后者在这个页面上永远超时）。用
`scripts/make-3d-images.js` 生成，可复现。

可选第四张：`http://127.0.0.1:8765/viewer3d`（3D 构象查看器）。

## 怎么拍

```bash
make gui                      # 起服务并打开浏览器
```

然后：

1. 窗口拉到 **1440×900** 左右（和旧图一致，README 里写着尺寸）；
2. 在左栏把「图类型」选成 `alcohol`、碳原子数填 `4`，点一次**生成**（这样中栏有图）；
3. 截图时**不要**让窗口标题栏/地址栏入镜，只留页面本身；
4. 存成 PNG 放进 `docs/images/`。

## 拍完要检查的一件事

截图里不能出现**真实的文件路径**。右栏「文件」面板会显示预览图的完整路径，
里面带着你的用户名 —— 之前就是因为这个泄露过（`/Users/<名字>/...`）。

两个办法：

- 拍之前把窗口拖到不显示路径的位置；或者
- 拍完用图像工具把那段文字抹掉（之前是这么做的）。

另外 `make check-privacy` **读不出像素里的字**，它只能提醒你"有截图，人工看一遍"。
