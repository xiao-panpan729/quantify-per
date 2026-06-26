@echo off
chcp 65001 >nul
echo ========================================
echo  IMA 知识库图片捕获 - mitmproxy
echo ========================================
echo.
echo  步骤1: 启动本窗口 (不要关闭)
echo  步骤2: 打开系统代理
echo    设置 → 网络 → 代理 → 手动
echo    地址: 127.0.0.1  端口: 8888
echo  步骤3: 打开 IMA Copilot 浏览知识库
echo  步骤4: 浏览完后关闭代理 + Ctrl+C 停止
echo.
echo  图片保存到: D:\ima_captures\
echo ========================================
echo.
pause
echo 启动代理...
mitmdump -s d:/quantify-per/tools/ima_capture.py -p 8888
pause
