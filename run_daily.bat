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

echo [1/15] 同步通达信数据（全市场）...
python update_from_tdx.py
if %errorlevel% neq 0 (
    echo [错误] 数据更新失败
    pause
    exit /b 1
)
echo.

echo [2/15] 成交量领导者筛选 + 宇宙同步...
python tools/volume_leader_screener.py --top 50 --update-rank --sync-universe --save
if %errorlevel% neq 0 (
    echo [警告] 成交量领导者筛选失败
)
echo.

echo [3/15] 生成跟踪信号（固定14只）...
python update_tracking.py
if %errorlevel% neq 0 (
    echo [错误] 信号更新失败
    pause
    exit /b 1
)
echo.

echo [4/15] 生成跟踪信号（成交量领导者）...
python update_volume_leaders.py
if %errorlevel% neq 0 (
    echo [警告] 成交量领导者信号更新失败（不影响其他步骤）
)
echo.

echo [5/15] 周期循环分析...
python cycle_engine.py --save
if %errorlevel% neq 0 (
    echo [错误] 周期分析失败
    pause
    exit /b 1
)
echo.

echo [6/15] 三层聚合分析...
python synthesize_report.py --save
if %errorlevel% neq 0 (
    echo [错误] 聚合分析失败
    pause
    exit /b 1
)
echo.

echo [7/15] 回测统计...
python backtest_signals.py --save
if %errorlevel% neq 0 (
    echo [错误] 回测统计失败
    pause
    exit /b 1
)
echo.

echo [8/15] 生成 v3 深度报告（固定14只）...
python gen_report_md.py
if %errorlevel% neq 0 (
    echo [警告] 报告生成失败，但数据已更新
)
echo.

echo [9/15] AI 自然语言日报（固定14只）...
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

echo [10/15] AI 日报（成交量领导者）...
python gen_volume_leader_report.py
if %errorlevel% neq 0 (
    echo [警告] 成交量领导者 AI 日报生成失败（不影响其他步骤）
)
echo.
echo.

echo [11/15] US 市场势能评分（ETF 52只）...
python tools/us_market/etf_momentum.py --save
if %errorlevel% neq 0 (
    echo [警告] US ETF 势能评分失败（不影响主流程）
)
echo.

echo [12/15] US 明星股动量评分（64只）...
python tools/us_market/star_stocks.py --save
if %errorlevel% neq 0 (
    echo [警告] US 明星股动量评分失败（不影响主流程）
)
echo.

echo [13/15] 消息面突发事件检测...
python tools/sentiment/shock_detector.py
if %errorlevel% neq 0 (
    echo [警告] 消息面检测失败（不影响主流程）
)
echo.

echo [14/15] US 宏观敏感度 + 跨市场映射...
python tools/us_market/macro_sensitivity.py
if %errorlevel% neq 0 (
    echo [警告] US 宏观映射失败（不影响主流程）
)
echo.

echo [14/15] 日本宏观 + 套息交易压力...
python tools/japan_macro.py --save
if %errorlevel% neq 0 (
    echo [警告] 日本宏观分析失败（不影响主流程）
)
echo.

echo ==========================================
echo 全部完成
echo.
echo   结构化报告: reports\daily\YYYYMMDD_v3.md
echo   AI日报:     reports\daily\YYYYMMDD_v3_nl.md
echo   量领AI日报: reports\volume_leader\YYYYMMDD_volume_leader_report.md
echo   US ETF势能: reports\us_market\YYYYMMDD_us_momentum.md
echo   US 明星股:  reports\us_market\YYYYMMDD_us_stars.md
echo   消息面冲击: signals\tracking\sentiment_shock.json
echo   日本宏观:   signals\tracking\japan_macro.json
echo.
echo 命令速查:
echo   python gen_report_md.py                       — 单独生成v3报告
echo   python ai_report_rewrite.py                   — 单独生成AI日报
echo   python gen_volume_leader_report.py            — 单独生成量领AI日报
echo   python tools/us_market/etf_momentum.py --search SMH  — 查US ETF势能
echo   python tools/us_market/star_stocks.py --search NVDA — 查US明星股
echo   python qa_tool.py                             — 终端快速胜率对比
