@echo off
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ==========================================
echo 午后全管道 — update_afternoon.bat
echo 所有散落的金融信息 → Obsidian 知识库 + GitHub
echo ==========================================
echo.

set ERR_CNT=0

echo [1/5] 知识星球 JSON → Obsidian...
python tools/convert_zsxq_to_md.py --auto --group=28888114545551
if %errorlevel% neq 0 (
    echo [警告] 知识星球转换失败（可能是无新数据）
    set /a ERR_CNT+=1
)
echo.

echo [2/5] IMA 知识库 OCR → Obsidian...
python tools/ocr_ima_kb.py
if %errorlevel% neq 0 (
    echo [警告] IMA OCR 失败
    set /a ERR_CNT+=1
)
echo.

echo [3/5] 公众号文章归档 → Obsidian wechat_daily/...
echo   （由 afternoon_pipeline.py 自动完成）
REM 此步骤在 afternoon_pipeline.py 内部执行
echo.

echo [4/5] 午后产业分析生成（多源→行业事件推导→个股）...
python tools/afternoon_pipeline.py --no-git
if %errorlevel% neq 0 (
    echo [ERROR] 产业分析生成失败，终止
    pause
    exit /b 1
)
echo.

echo [5/5] 发布产业分析到 GitHub Pages 网页...
python _publish_industry.py
if %errorlevel% equ 0 (
    echo  ✓ GitHub Pages 发布完成
) else (
    echo  ⚠ 发布失败（可能是文件已存在）
)
echo.
REM 打开今日生成的报告（如果存在）
for /f %%i in ('powershell -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
if exist "reports\industry_daily\%TODAY%.md" (
    start "" "reports\industry_daily\%TODAY%.md"
)
