"""示例插件：写一个自己的图类型（阶段④）。

删掉这个文件不会影响任何内置功能 —— 它只是演示插件怎么写。
"""


def register(api):
    def runner(out, n_carbons=4, formats=("png",), dpi=None, transparent=False, **_ignored):
        # 用与内置图相同的底层工具作画（PNG / SVG 一份代码）
        width, height = 900, 160
        scale = api.core.dpi_scale(width, height, dpi)
        surface = api.surface(width, height, scale=scale, svg="svg" in formats)
        surface.text((30, 40), "C-H on C%d = %d" % (n_carbons, 2 * n_carbons + 1),
                     font=api.core.fonts(28, True), fill=api.core.INK)
        surface.line([(30, 90), (30 + 40 * n_carbons, 90)],
                     fill=api.core.OXYGEN_RED, width=10)
        report = api.core.save_figure(surface, out, formats=formats, dpi=dpi,
                                      transparent=transparent)
        return api.figures.FigureResult("chcount_bar", report)

    api.add_figure(
        "chcount_bar", runner,
        title="C-H 数量条形（示例插件）",
        description="把某碳数烷基的 C-H 数目画成一条比例条。",
        params=[api.int_param("n_carbons", label="碳原子数", default=4, minimum=1, maximum=12)],
        default_filename="chcount_bar.png",
    )
