@echo off
REM szrehot paipai: launch ShadowBot with remote debugging for fetch_paipai_hot.py CDP reuse.
REM Login state (Taobao) persists in C:\ChromeDebug across runs.
REM NEVER taskkill the user main browser; this instance uses its own profile dir.
if not exist "C:\ChromeDebug" mkdir "C:\ChromeDebug"
start "" "D:\Program Files\ShadowBot\ShadowBot Browser\Application\ShadowBotBrowser.exe" --remote-debugging-port=9222 --user-data-dir="C:\ChromeDebug" --no-first-run --no-default-browser-check about:blank
echo ShadowBot debug instance launched on port 9222 with profile C:\ChromeDebug
echo If Taobao login expired: re-copy login files from %LOCALAPPDATA%\ShadowBotBrowser\User Data
echo   (robocopy, exclude Cache/Code Cache/GPUCache and LOCK/Singleton/lockfile) or scan QR once in popup.
