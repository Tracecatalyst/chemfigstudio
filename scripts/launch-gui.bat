@echo off
rem 双击启动 chemfigstudio 图形界面（Windows）。
rem
rem 前提：装了 Python 3.9+ 和 Pillow。没装的话这个脚本会告诉你装什么。
rem 输出目录优先级：命令行参数 > %USERPROFILE%\Desktop\chemfig-out
rem
rem 想固定到别处：设 OUTDIR 环境变量，或者直接命令行跑
rem   python -m chemfigstudio.cli gui --outdir D:\somewhere
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

rem 切到仓库根（本脚本在 scripts\ 下）
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\src"

rem ── 找 Python：优先 py 启动器，其次 python ──
set "PY="
where py >nul 2>&1 && py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [错误] 找不到 Python 3。
    echo         请到 https://www.python.org/downloads/ 安装 Python 3.9 或更高版本，
    echo         安装时记得勾选 "Add Python to PATH"。
    pause
    exit /b 3
)

rem ── Pillow 是唯一的硬依赖 ──
%PY% -c "import PIL" >nul 2>&1
if errorlevel 1 (
    echo 还没装 Pillow —— 正在帮你装（只装到你自己的用户目录，不动系统）...
    %PY% -m pip install --user --quiet pillow
    if errorlevel 1 (
        echo [错误] 装 Pillow 失败。可以手动跑：
        echo         %PY% -m pip install --user pillow
        pause
        exit /b 3
    )
    echo   Pillow 装好了。
)

rem ── RDKit 是可选的（能用任意 SMILES 和 3D）──
%PY% -c "import rdkit" >nul 2>&1
if errorlevel 1 (
    echo 提示：没装 RDKit —— 常用图、骨架编辑、molblock/SDF 进出都正常，
    echo       但任意 SMILES 和 3D 构象不可用。想要的话跑：
    echo         %PY% -m pip install --target .pylibs rdkit numpy
)

rem ── 输出目录 ──
if "%~1"=="" (
    set "OUTDIR=%USERPROFILE%\Desktop\chemfig-out"
) else (
    set "OUTDIR=%~1"
)
if not exist "%OUTDIR%" mkdir "%OUTDIR%" 2>nul

echo.
echo chemfigstudio —— 输出目录：%OUTDIR%
echo 浏览器会自动打开 http://127.0.0.1:8765/
echo 关掉这个窗口即可停止服务。
echo.

%PY% -m chemfigstudio.gui.server --outdir "%OUTDIR%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo 服务异常退出（代码 %RC%）。窗口留着方便你看错误信息。
    echo 日志在：%LOCALAPPDATA%\chemfigstudio 或者上面打印的路径。
    pause
)
endlocal
