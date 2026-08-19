@echo off
REM 以调试端口 9222 启动【专用自动化 Chrome 环境】（供 szrehot 法拍面积抓取使用）
REM 方案 A：专用目录 C:\ChromeDebug，与日常浏览器隔离；登录态永久保存在该目录
REM 用法：
REM   1. 首次使用：双击本脚本启动 Chrome，在弹出的窗口里登录淘宝（勾选记住登录状态）
REM   2. 之后每次：先双击本脚本（或手动以同样命令启动），再运行法拍脚本即可复用登录态
REM 注意：本脚本会关闭当前所有 Chrome 窗口，请先保存重要内容
taskkill /IM chrome.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
if not exist "C:\ChromeDebug" mkdir "C:\ChromeDebug"
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug" --no-first-run --no-default-browser-check
echo Chrome 专用调试环境已启动（端口 9222，目录 C:\ChromeDebug）。
echo 首次使用请在弹出的窗口中登录淘宝；后续运行法拍脚本将自动复用登录态。
pause
