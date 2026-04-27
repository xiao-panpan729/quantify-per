@echo off
chcp 65001 >nul

REM 自动获取脚本所在目录
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ==========================================
echo 数据更新 + 快照生成
echo 项目路径: %SCRIPT_DIR%
echo ==========================================
echo.

echo [1/2] 更新通达信数据...
python update_from_tdx.py
if %errorlevel% neq 0 (
    echo [错误] 数据更新失败
    pause
    exit /b 1
)

echo.
echo [2/2] 生成跟踪快照...
python update_tracking.py
if %errorlevel% neq 0 (
    echo [错误] 快照生成失败
    pause
    exit /b 1
)

echo.
echo ==========================================
echo 数据更新完成！
echo ==========================================
pause
