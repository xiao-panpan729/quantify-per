@echo off
chcp 65001 >nul
echo ============ IMA 捕获统计 ============
if not exist D:\ima_captures\ (
    echo 尚无捕获数据
    goto :eof
)
dir /s /b D:\ima_captures\*.png D:\ima_captures\*.jpg D:\ima_captures\*.jpeg D:\ima_captures\*.webp 2>nul | find /c /v "" > tmp_count.txt
set /p count=<tmp_count.txt
del tmp_count.txt
echo 总图片数: %count%
echo.
echo 按日期分布:
for /d %%d in (D:\ima_captures\*) do (
    dir /b "%%d" 2>nul | find /c /v "" > tmp_c.txt
    set /p c=<tmp_c.txt
    del tmp_c.txt
    for %%f in (%%d) do echo   %%~nxf: !c! 张
)
echo.
echo 最新文件:
dir /t:w /o:d D:\ima_captures\* 2>nul
pause
