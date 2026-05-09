@echo off
chcp 65001 >nul

REM 自动获取脚本所在目录
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ==========================================
echo 每日数据更新 v3 — 全市场同步 + 信号 + 统计
echo 项目路径: %SCRIPT_DIR%
echo ==========================================
echo.

echo [1/5] 同步通达信数据（全市场）...
python update_from_tdx.py
if %errorlevel% neq 0 (
    echo [错误] 数据更新失败
    pause
    exit /b 1
)
echo.

echo [2/5] 生成跟踪信号...
python update_tracking.py
if %errorlevel% neq 0 (
    echo [错误] 信号更新失败
    pause
    exit /b 1
)
echo.

echo [3/5] 周期循环分析...
python cycle_engine.py --save
if %errorlevel% neq 0 (
    echo [错误] 周期分析失败
    pause
    exit /b 1
)
echo.

echo [4/5] 回测统计...
python backtest_signals.py --save
if %errorlevel% neq 0 (
    echo [错误] 回测统计失败
    pause
    exit /b 1
)
echo.

echo [5/5] 生成 v3 深度报告...
python gen_report_md.py
if %errorlevel% neq 0 (
    echo [警告] 报告生成失败，但数据已更新
)
echo.

echo ==========================================
echo 全部完成 — reports\daily\YYYYMMDD_v3.md
echo.
echo 命令速查:
echo   python gen_report_md.py              — 单独生成v3报告
echo   python qa_tool.py                    — 终端快速胜率对比
echo   python qa_tool.py sz159740 min5      — 单标的信号流水
echo   python cycle_engine.py --save        — 单独跑周期分析
echo ==========================================
pause
