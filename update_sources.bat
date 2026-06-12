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
echo 10步数据采集 → gen_source_summary.py --ai
echo 输出: reports/sources/YYYYMMDD_sources.md
echo ==========================================
echo.

set ERR_CNT=0

echo [1/10] 微信公众号文章（7个信源）...
python _fetch_articles.py
if %errorlevel% neq 0 (
    echo [警告] 公众号拉取失败
    set /a ERR_CNT+=1
)
echo.

echo [2/10] 消息面突发事件检测（鼓掌WS + 东财 + 见闻）...
python tools/sentiment/shock_detector.py
if %errorlevel% neq 0 (
    echo [警告] 消息面检测失败
    set /a ERR_CNT+=1
)
echo.

echo [3/10] 全球流动性全景（BTC/VIX/DXY/M2/社融）...
python tools/liquidity_monitor.py --save
if %errorlevel% neq 0 (
    echo [警告] 流动性监测失败
    set /a ERR_CNT+=1
)
echo.

echo [4/10] 中国宏观快照（M2/SHIBOR/CPI/PMI + 国债利率/汇率/商品）...
python tools/macro_sensitivity.py --classify
if %errorlevel% neq 0 (
    echo [警告] 中国宏观快照失败
    set /a ERR_CNT+=1
)
echo.

echo [5/10] US宏观敏感度（Fed利率/CPI/PMI/非农）...
python tools/us_market/macro_sensitivity.py
if %errorlevel% neq 0 (
    echo [警告] US宏观分析失败
    set /a ERR_CNT+=1
)
echo.

echo [6/10] 日本宏观 + 套息交易压力...
python tools/japan_macro.py --save
if %errorlevel% neq 0 (
    echo [警告] 日本宏观分析失败
    set /a ERR_CNT+=1
)
echo.

echo [7/10] 基本面数据层（Rolling FM / TypeAB / CAPEX）...
python -m tools.fundamental.data_layer
if %errorlevel% neq 0 (
    echo [警告] 基本面数据层失败
    set /a ERR_CNT+=1
)
echo.

echo [8/10] US ETF 势能评分（52只）...
python tools/us_market/etf_momentum.py --save
if %errorlevel% neq 0 (
    echo [警告] US ETF 势能评分失败
    set /a ERR_CNT+=1
)
echo.

echo [9/10] US 明星股动量评分（64只）...
python tools/us_market/star_stocks.py --save
if %errorlevel% neq 0 (
    echo [警告] US 明星股动量评分失败
    set /a ERR_CNT+=1
)
echo.

echo [10/10] 概念链轮动排名...
python tools/us_market/concept_chains.py --momentum
if %errorlevel% neq 0 (
    echo [警告] 概念链轮动失败
    set /a ERR_CNT+=1
)
echo.

echo ==========================================
echo 生成信源摘要报告...
python gen_source_summary.py --ai
if %errorlevel% neq 0 (
    echo [警告] 摘要生成失败
    set /a ERR_CNT+=1
)
REM 弹窗推迟到 gen_daily_brief 之后

echo.
echo [11/13] 信号事件流提取（关键词匹配）...
python tools/signal_extractor.py
if %errorlevel% neq 0 (
    echo [警告] 信号提取失败
    set /a ERR_CNT+=1
)
echo.

echo [12/13] ★深度精读（LLM全量分析公众号）...
python tools/signal_deep_reader.py
if %errorlevel% neq 0 (
    echo [警告] 深度精读失败
    set /a ERR_CNT+=1
)
echo.

echo [13/13] ★观点聚合+共振判断...
python gen_daily_brief.py
if %errorlevel% neq 0 (
    echo [警告] 观点聚合失败
    set /a ERR_CNT+=1
) else (
    REM 全部完成后才弹窗，确保是完整报告
    for /f "delims=" %%f in ('dir /b /o-d reports\sources\*_sources.md 2^>nul') do (
        start "" "reports\sources\%%f"
        goto :endopen
    )
)
:endopen
echo.

echo ==========================================
echo 	更新完成
if %ERR_CNT% gtr 0 (
    echo 	警告: %ERR_CNT% 个步骤有异常
)
echo ==========================================
echo.
