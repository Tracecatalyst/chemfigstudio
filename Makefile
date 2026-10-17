# chemfigstudio — 常用命令（GUI / CLI / 测试 / 出图）
#
# 所有命令都假定在仓库根目录（tools/chemfig-studio/）执行。
# 无需安装：源码在 src/，靠 PYTHONPATH 引入。

PY      ?= python3
SRC     := src
OUT     ?= examples/out
PYTHONPATH_ENV := PYTHONPATH=$(SRC)

# 不写字节码缓存：沙箱/只读环境下更稳，也少一个要 gitignore 的目录
export PYTHONDONTWRITEBYTECODE := 1


help:
	@echo "make test      - 跑全部单元测试（core / figures / cli / gui）"
	@echo "make figures   - 用 CLI 生成 4 张示例图到 $(OUT)/"
	@echo "make gui       - 启动本地网页 GUI（默认 http://127.0.0.1:8765）"
	@echo "make app       - 打开 macOS 原生应用（首次会自动编译）"
	@echo "make bundle    - 编译 dist/ChemFigStudio.app（内嵌 Pillow + RDKit/numpy，约 113 MB；有 RDKit 时）"
	@echo "make bundle-lite - 同上但不内嵌 Pillow（约 400 KB，目标机需自备 Pillow）"
	@echo "make bundle-universal - 通用二进制（arm64 + x86_64，lipo 合并）"
	@echo "make lint      - 语法编译检查（compileall）"
	@echo "make doctor    - 打印环境自检（python / Pillow / 字体 / RDKit）"
	@echo "make clean     - 清掉 __pycache__ 与临时输出"
	@echo ""
	@echo "覆盖变量示例： make gui OUT=~/Desktop/chemfig-out"

test:
	@# 测试隔离：不读用户真实设置（否则语言/DPI/默认输出目录会让结果随机器变化）
	@rm -rf dist/test-cfg
	PYTHONPATH=$(SRC) CHEMFIG_CONFIG_DIR=$(CURDIR)/dist/test-cfg CHEMFIG_LANG=zh-CN \
	  $(PY) -m unittest discover -s tests -v

showcase:
	@# Structure-import showcase (renders a dozen real molecules from SMILES)
	$(PYTHONPATH_ENV) $(PY) scripts/make-showcase.py

figures:
	@mkdir -p $(OUT)
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli alcohol --n 4 --name butan-1-ol --dh -36.1 -o $(OUT)/图1_丁醇.png
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli series     -o $(OUT)/图3_醇系列.png
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli reaction   -o $(OUT)/图2_燃烧反应.png
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli chcount    -o $(OUT)/图4_丁醇的CH键.png
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli enthalpy   -o $(OUT)/图5_燃烧焓对比.png
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli esterification --acid-carbons 2 --alcohol-carbons 2 -o $(OUT)/图6_酯化反应.png

gui:
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.gui.server --outdir $(OUT)

app:
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli app

bundle:
	./scripts/build-macos-app.sh

bundle-lite:
	./scripts/build-macos-app.sh --no-vendor


typecheck:
	@# .pylibs 里的控制台脚本 shebang 不带 PYTHONPATH，所以要 -m 跑
	@if [ -d "$(CURDIR)/.pylibs/mypy" ]; then PYTHONPATH=$(CURDIR)/.pylibs $(PY) -m mypy ; \
	elif command -v mypy >/dev/null 2>&1; then mypy ; \
	else echo "SKIPPED：mypy 未安装（装法：python3 -m pip install --target ./.pylibs mypy）"; fi

format:
	@if [ -d "$(CURDIR)/.pylibs/black" ]; then PYTHONPATH=$(CURDIR)/.pylibs $(PY) -m black src tests ; \
	elif command -v black >/dev/null 2>&1; then black src tests ; \
	else echo "SKIPPED：black 未安装（装法：python3 -m pip install --target ./.pylibs black）"; fi

lint:
	$(PY) -c "import ast, pathlib; files = sorted(list(pathlib.Path('$(SRC)').rglob('*.py')) + list(pathlib.Path('tests').rglob('*.py'))); [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print('语法检查 OK：%d 个文件' % len(files))"

doctor:
	$(PYTHONPATH_ENV) $(PY) -m chemfigstudio.cli doctor

bundle-universal:
	@# 通用二进制（Apple Silicon + Intel）：分别编译再 lipo 合并。
	@# 中间切片放**临时目录**，跑完必删 —— 否则 dist/ 里会堆出三四个同名同图标的 .app，
	@# 人和 Dock 都分不清该点哪个（踩过）。
	@# 注意：BSD mktemp -d 的模板**不会**替你建父目录，而 $TMPDIR 结尾常带 /，
	@# 拼成 /var/folders/.../T//chemfig-universal.XXXX 时父目录不存在就会失败
	@# （踩过：arm64 抢到可用路径、x86_64 就炸）。所以显式擦掉尾部斜杠。
	@TMPBASE=$${TMPDIR:-/tmp}; TMPBASE=$${TMPBASE%/}; \
	WORK=$$(mktemp -d "$$TMPBASE/chemfig-universal.XXXXXX"); \
	trap 'rm -rf "$$WORK"' EXIT INT TERM; \
	bash scripts/build-macos-app.sh --arch arm64 --out "$$WORK/arm64" && \
	bash scripts/build-macos-app.sh --arch x86_64 --out "$$WORK/x86_64" && \
	bash scripts/make-universal.sh --arm "$$WORK/arm64/ChemFigStudio.app" \
	                               --intel "$$WORK/x86_64/ChemFigStudio.app"

apps:
	@# 告诉你 dist/ 里哪个是"拷给别人的那个"
	@$(PY) scripts/list-apps.py

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf /tmp/chemfigstudio-*
