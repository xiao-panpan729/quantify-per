@echo off
chcp 65001 >nul

REM 自动获取脚本所在目录
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM 加载 .env 环境变量（API Key 等，不提交 git）
if exist "%SCRIPT_DIR%.env" (
    for /f "usebackq tokens=1,2 delims==" %%a in ("%SCRIPT_DIR%.env") do (
        if not "%%a"=="" if not "%%b"=="" (
            set "%%a=%%b"
        )
    )
)

echo ==========================================
echo 每日数据更新 v3 — 全市场同步 + 信号 + 统计
echo 项目路径: %SCRIPT_DIR%
echo ==========================================
echo.

REM 信源更新请单独运行 update_sources.bat（公众号/消息面/宏观/US市场）
echo.

echo [1/10] 同步通达信数据（全市场）...
python update_from_tdx.py
if %errorlevel% neq 0 (
    echo [错误] 数据更新失败
    pause
    exit /b 1
)
echo.

echo [2/10] 成交量领导者筛选 + 宇宙同步...
python tools/volume_leader_screener.py --top 50 --update-rank --sync-universe --save
if %errorlevel% neq 0 (
    echo [警告] 成交量领导者筛选失败
)
echo.

echo [3/10] 生成跟踪信号（固定14只）...
python update_tracking.py
if %errorlevel% neq 0 (
    echo [错误] 信号更新失败
    pause
    exit /b 1
)
echo.

echo [4/10] 生成跟踪信号（成交量领导者）...
python update_volume_leaders.py
if %errorlevel% neq 0 (
    echo [警告] 成交量领导者信号更新失败（不影响其他步骤）
)
echo.

echo [5/10] 周期循环分析...
python run_cycle.py --save
if %errorlevel% neq 0 (
    echo [错误] 周期分析失败
    pause
    exit /b 1
)
echo.

echo [6/10] 三层聚合分析...
python synthesize_report.py --save
if %errorlevel% neq 0 (
    echo [错误] 聚合分析失败
    pause
    exit /b 1
)
echo.

echo [7/10] 回测统计...
python backtest_signals.py --save
if %errorlevel% neq 0 (
    echo [错误] 回测统计失败
    pause
    exit /b 1
)
echo.

echo [8/10] 生成 v3 深度报告（固定14只）...
python gen_report_md.py
if %errorlevel% neq 0 (
    echo [警告] 报告生成失败，但数据已更新
)
echo.

echo [9/10] AI 自然语言日报（固定14只）...
python ai_report_rewrite.py
if %errorlevel% neq 0 (
    echo [警告] AI 日报生成失败（不影响其他步骤）
) else (
    echo [弹出] AI 日报
)

REM 打开最新 AI 日报
for /f "delims=" %%f in ('dir /b /o-d reports\daily\*_v3_nl.md 2^>nul') do (
    start "" "reports\daily\%%f"
    goto :endopen
)
:endopen
echo.

echo [10/10] AI 日报（成交量领导者）...
python gen_volume_leader_report.py
if %errorlevel% neq 0 (
    echo [警告] 成交量领导者 AI 日报生成失败（不影响其他步骤）
)
echo.
echo.

echo ==========================================
echo 全部完成
echo.
echo   结构化报告: reports\daily\YYYYMMDD_v3.md
echo   AI日报:     reports\daily\YYYYMMDD_v3_nl.md
echo   量领AI日报: reports\volume_leader\YYYYMMDD_volume_leader_report.md
echo   信源摘要:   reports\sources\YYYYMMDD_sources.md
echo.
echo 命令速查:
echo   python gen_report_md.py                       — 单独生成v3报告
echo   python ai_report_rewrite.py                   — 单独生成AI日报
echo   python gen_volume_leader_report.py            — 单独生成量领AI日报
echo   python tools/us_market/etf_momentum.py --search SMH  — 查US ETF势能
echo   python tools/us_market/star_stocks.py --search NVDA — 查US明星股
echo   python qa_tool.py                             — 终端快速胜率对比
