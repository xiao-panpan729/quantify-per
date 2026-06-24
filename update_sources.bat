@echo off
chcp 65001 >nul

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
echo 信源日报 — update_sources.bat
echo 17步数据采集 → AI分析 → 发布
echo 输出: reports/sources/YYYYMMDD_sources.md
echo ==========================================
echo.

set ERR_CNT=0

echo [1/17] 微信公众号文章（8个信源）...
python _fetch_articles.py
if %errorlevel% neq 0 (
    echo ==========================================
    echo   ❌ 公众号 API 失效，拉取失败
    echo   请更新 API 凭证后重新运行整个流程
    echo   update_sources.bat
    echo ==========================================
    pause
    exit /b 1
)
echo.

echo [2/17] 消息面突发事件检测（鼓掌WS + 东财 + 见闻）...
python tools/sentiment/shock_detector.py
if %errorlevel% neq 0 (
    echo [警告] 消息面检测失败
    set /a ERR_CNT+=1
)
echo.

echo [3/17] US 市场异动提取（从快讯提取个股/板块/ETF异动）...
python tools/us_market/us_movers_extract.py --save
if %errorlevel% neq 0 (
    echo [警告] US 异动提取失败
    set /a ERR_CNT+=1
)
echo.

echo [4/17] 全球流动性全景（BTC/VIX/DXY/M2/社融）...
python tools/liquidity_monitor.py --save
if %errorlevel% neq 0 (
    echo [警告] 流动性监测失败
    set /a ERR_CNT+=1
)
echo.

echo [5/17] 中国宏观快照（M2/SHIBOR/CPI/PMI + 国债利率/汇率/商品）...
python tools/macro_sensitivity.py --classify
if %errorlevel% neq 0 (
    echo [警告] 中国宏观快照失败
    set /a ERR_CNT+=1
)
echo.

echo [6/17] US宏观敏感度（Fed利率/CPI/PMI/非农）...
python tools/us_market/macro_sensitivity.py
if %errorlevel% neq 0 (
    echo [警告] US宏观分析失败
    set /a ERR_CNT+=1
)
echo.

echo [7/17] 日本宏观 + 套息交易压力...
python tools/japan_macro.py --save
if %errorlevel% neq 0 (
    echo [警告] 日本宏观分析失败
    set /a ERR_CNT+=1
)
echo.

echo [8/17] 基本面数据层（Rolling FM / TypeAB / CAPEX）...
python -m tools.fundamental.data_layer
if %errorlevel% neq 0 (
    echo [警告] 基本面数据层失败
    set /a ERR_CNT+=1
)
echo.

echo [9/17] US ETF 势能评分（52只）...
python tools/us_market/etf_momentum.py --save
if %errorlevel% neq 0 (
    echo [警告] US ETF 势能评分失败
    set /a ERR_CNT+=1
)
echo.

echo [10/17] US 明星股动量评分（64只）...
python tools/us_market/star_stocks.py --save
if %errorlevel% neq 0 (
    echo [警告] US 明星股动量评分失败
    set /a ERR_CNT+=1
)
echo.

echo [11/17] 概念链轮动排名...
python tools/us_market/concept_chains.py --momentum
if %errorlevel% neq 0 (
    echo [警告] 概念链轮动失败
    set /a ERR_CNT+=1
)
echo.

echo ==========================================
echo [12/17] 生成信源摘要报告...
python gen_source_summary.py
if %errorlevel% neq 0 (
    echo [警告] 摘要生成失败
    set /a ERR_CNT+=1
)
REM 弹窗推迟到 gen_daily_brief 之后

echo.
echo [13/17] 信号事件流提取（关键词匹配）...
python tools/signal_extractor.py
if %errorlevel% neq 0 (
    echo [警告] 信号提取失败
    set /a ERR_CNT+=1
)
echo.

echo [14/17] ★深度精读（LLM全量分析公众号）...
python tools/signal_deep_reader.py
if %errorlevel% neq 0 (
    echo [警告] 深度精读失败
    set /a ERR_CNT+=1
)
echo.

echo [15/17] ★观点聚合+共振判断...
python gen_daily_brief.py
if %errorlevel% neq 0 (
    echo [警告] 观点聚合失败
    set /a ERR_CNT+=1
)
echo.

echo 检查变量分类器候选项...
python -c "from tools.variable_taxonomy import get_candidates; pending=get_candidates('pending'); print(len(pending))" > "%TEMP%\cand_cnt.txt" 2>nul
set /p CAND_CNT=<"%TEMP%\cand_cnt.txt"
if defined CAND_CNT (
    if %CAND_CNT% gtr 0 (
        echo ==========================================
        echo   ⚠ %CAND_CNT% 个新话题未分类 -^> /taxonomy-review
        echo ==========================================
    )
    del "%TEMP%\cand_cnt.txt" 2>nul
)
echo.

echo ==========================================
echo [16/17] Claude Code 联网验证+数据填充（话题验证 → 数据段占位符）
echo     按 prompts/source_analysis_prompt.md 执行全流程（第-2步→第0.75步）
echo     关键约束：不得修改表格格式（ETF/个股的英文名列必须保留）
echo ==========================================
set "CLAUDE_BIN=C:\Users\Administrator\.workbuddy\binaries\node\versions\22.12.0\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
if exist "%CLAUDE_BIN%" (
    "%CLAUDE_BIN%" -p "按 prompts/source_analysis_prompt.md 完整流程执行：第-2步通读信源→第-1步读sources.md→第0步数据自检→第0.25步知识缺口扫描（逐话题判断触发条件，命中则搜索追加）→第0.3步海外映射验证（🪝 区块的外资观点搜索）→第0.5步叙事定位→第0.75步数据段占位符填充。关键：不得修改表格格式（ETF/个股的英文名列必须保留，不可替换为中文）。" --permission-mode acceptEdits --print
    )
)

echo.
echo ==========================================
echo [17/17] 发布到 GitHub Pages...
python _publish_report.py
if %errorlevel% neq 0 (
    echo [警告] Pages 发布失败
    set /a ERR_CNT+=1
)

echo.
echo ==========================================
echo 全部完成！打开今日信源日报...
echo ==========================================
for /f %%i in ('powershell -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
start "" "reports/sources/%TODAY%_sources.md"