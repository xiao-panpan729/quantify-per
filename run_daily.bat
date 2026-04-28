@echo off
chcp 65001 >nul

REM 自动获取脚本所在目录
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ==========================================
echo 每日数据更新 + 机会扫描
echo 项目路径: %SCRIPT_DIR%
echo ==========================================
echo.

echo [1/3] 更新通达信数据...
python update_from_tdx.py
if %errorlevel% neq 0 (
    echo [错误] 数据更新失败，停止后续步骤
    pause
    exit /b 1
)

echo.
echo [2/3] 生成跟踪快照...
python update_tracking.py
if %errorlevel% neq 0 (
    echo [错误] 快照生成失败，停止后续步骤
    pause
    exit /b 1
)

echo.
echo [3/3] 机会扫描 + 生成报告...
python scan_opportunities.py --report --ai
REM AI分析会自动切换: NVIDIA-v4 - NVIDIA-v3 - Cloudflare - SiliconFlow

echo.
echo ==========================================
echo 全部完成
echo 报告位置: reports\daily\
echo CSV日志:  reports\judgement_log.csv
echo.
echo 其他命令:
echo   python update_tracking.py --verify   (pytdx数据抽查验证)
echo   python scan_opportunities.py --report --ai   (单独生成AI分析报告)
echo ==========================================
pause
