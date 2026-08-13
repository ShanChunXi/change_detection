@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Change Detection Launcher — SuperMap Cup
REM  双击启动 → 交互菜单
REM  首次使用自动引导配置
REM ============================================================

set "SCRIPT=%~dp0src\change_detection_ui.py"
set "CONFIG=%~dp0src\config.json"

REM ---- 1. 找 SuperMap Python ----
set "PY="

REM 1a) 从 config.json 读取
if exist "%CONFIG%" (
    for /f "tokens=2 delims=: " %%a in ('findstr "python_path" "%CONFIG%"') do (
        set "RAW=%%~a"
        set "RAW=!RAW:"=!
        set "RAW=!RAW:,=!
        if exist "!RAW!" set "PY=!RAW!"
    )
)

REM 1b) 标准安装路径
if "%PY%"=="" (
    for %%d in (F: D: E: C:) do (
        if exist "%%d\supermap\supermap-iobjectspy-env-gpu-2026-win64\conda\python.exe" (
            set "PY=%%d\supermap\supermap-iobjectspy-env-gpu-2026-win64\conda\python.exe"
        )
        if "!PY!"=="" if exist "%%d\supermap\supermap-iobjectspy-env-2026-win64\conda\python.exe" (
            set "PY=%%d\supermap\supermap-iobjectspy-env-2026-win64\conda\python.exe"
        )
    )
)

REM 1c) 系统 PATH 中的 python
if "%PY%"=="" (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

REM ---- 2. 验证是否真的是 SuperMap Python ----
if not "%PY%"=="" (
    "%PY%" -c "import iobjectspy" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo  [警告] 找到的 Python 无法导入 iobjectspy，不是 SuperMap Python
        echo         %PY%
        set "PY="
    )
)

REM ---- 3. 找不到则交互配置 ----
if "%PY%"=="" goto :interactive_setup

REM ---- 4. 启动 ----
goto :launch

REM ============================================================
REM  交互式配置（首次使用 / Python 路径失效时触发）
REM ============================================================
:interactive_setup
echo.
echo  +==========================================================+
echo  ^|        SuperMap Python 未找到，请按提示首次配置            ^|
echo  +==========================================================+
echo.
echo   请将 SuperMap iObjects Python 的安装目录拖入此窗口
echo   （通常是包含 conda/python.exe 的目录）
echo   示例: F:\supermap\supermap-iobjectspy-env-gpu-2026-win64
echo.
set /p "USER_INPUT=  ^> "
if "!USER_INPUT!"=="" (
    echo  未输入，使用系统 PATH 中的 python（可能不是 SuperMap Python）
    set "PY=python"
    goto :save_config_quick
)

set "USER_INPUT=!USER_INPUT:"=!"
if "!USER_INPUT:~-1!"=="\" set "USER_INPUT=!USER_INPUT:~0,-1!"

REM 尝试找到 python.exe
set "PY_FOUND="
if exist "!USER_INPUT!\python.exe" set "PY_FOUND=!USER_INPUT!\python.exe"
if "!PY_FOUND!"=="" if exist "!USER_INPUT!\conda\python.exe" set "PY_FOUND=!USER_INPUT!\conda\python.exe"
if "!PY_FOUND!"=="" if exist "!USER_INPUT!\bin\python.exe" set "PY_FOUND=!USER_INPUT!\bin\python.exe"

if "!PY_FOUND!"=="" (
    for /r "!USER_INPUT!" %%f in (python.exe) do (
        if "!PY_FOUND!"=="" set "PY_FOUND=%%f"
    )
)

if "!PY_FOUND!"=="" (
    echo  [错误] 在指定位置未找到 python.exe，请重新输入
    goto :interactive_setup
)

"!PY_FOUND!" -c "import iobjectspy" >nul 2>&1
if errorlevel 1 (
    echo  [错误] 该 Python 无法导入 iobjectspy，不是 SuperMap Python
    echo         !PY_FOUND!
    goto :interactive_setup
)

set "PY=!PY_FOUND!"
set "CFG_PYTHON=!PY_FOUND:\=/!"
echo  [OK] 找到并验证: !PY_FOUND!

REM 快速保存配置（只写 python_path，其余路径自动检测）
:save_config_quick
if "%CFG_PYTHON%"=="" set "CFG_PYTHON=python"

REM 尝试自动检测标准路径
set "CFG_JAVA="
set "CFG_IOBJECTS="
set "CFG_RESOURCES="
for %%d in (F: D: E: C:) do (
    if "!CFG_JAVA!"=="" if exist "%%d\supermap\supermap-iobjectsjava-2026-win-all\jre1.8_x64" (
        set "CFG_JAVA=%%d/supermap/supermap-iobjectsjava-2026-win-all/jre1.8_x64"
    )
    if "!CFG_IOBJECTS!"=="" if exist "%%d\supermap\supermap-iobjectsjava-2026-win-all\Bin" (
        set "CFG_IOBJECTS=%%d/supermap/supermap-iobjectsjava-2026-win-all/Bin"
    )
    if "!CFG_RESOURCES!"=="" if exist "%%d\supermap\supermap-iobjectspy-resources_ml-2025u1\resources_ml" (
        set "CFG_RESOURCES=%%d/supermap/supermap-iobjectspy-resources_ml-2025u1/resources_ml"
    )
    if "!CFG_RESOURCES!"=="" if exist "%%d\supermap\supermap-iobjectspy-resources_ml-2026\resources_ml" (
        set "CFG_RESOURCES=%%d/supermap/supermap-iobjectspy-resources_ml-2026/resources_ml"
    )
)

REM 写入 config.json
(
    echo {
    echo     "java_home": "!CFG_JAVA!",
    echo     "iobjects_bin": "!CFG_IOBJECTS!",
    echo     "resources_ml": "!CFG_RESOURCES!",
    echo     "python_path": "!CFG_PYTHON!",
    echo     "last_params": {
    echo         "before": "",
    echo         "after": "",
    echo         "out": "result.udbx",
    echo         "model": "building",
    echo         "gpu": 0,
    echo         "out_format": "udbx",
    echo         "classify": true,
    echo         "min_change_area": 0
    echo     }
    echo }
) > "%CONFIG%"

echo.
echo  [OK] 配置已保存
if "!CFG_JAVA!"==""      echo  [WARN] java_home 未自动检测到，后续可通过菜单 [6] 手动配置
if "!CFG_IOBJECTS!"==""  echo  [WARN] iobjects_bin 未自动检测到，后续可通过菜单 [6] 手动配置
if "!CFG_RESOURCES!"=="" echo  [WARN] resources_ml 未自动检测到，后续可通过菜单 [6] 手动配置
echo.

REM ============================================================
REM  启动程序
REM ============================================================
:launch
if not exist "%SCRIPT%" (
    echo [ERROR] 脚本不存在: %SCRIPT%
    pause
    exit /b 1
)

cls
echo.
echo   正在启动 SuperMap 变化检测工具...
echo   首次加载约需 60 秒（初始化 JAR 组件），请耐心等待
echo.

REM 抑制 JAR 复制输出，只保留 Python 正常输出
if "%~1"=="" (
    "%PY%" "%SCRIPT%" 2>nul
) else (
    "%PY%" "%SCRIPT%" %* 2>nul
)

REM 如果 2>nul 导致退出码异常，用 fallback
if errorlevel 1 (
    if "%~1"=="" (
        "%PY%" "%SCRIPT%"
    ) else (
        "%PY%" "%SCRIPT%" %*
    )
)

pause
