@echo off
chcp 65001 >nul
setlocal

rem UI Mirror başlatıcı: hangi klasörden çalıştırılırsa çalıştırılsın doğru dizine geçer.
cd /d "%~dp0.."

if exist "%~dp0dist\UI-Mirror.exe" (
    start "" "%~dp0dist\UI-Mirror.exe" %*
    exit /b 0
)

python -m ui_mirror --gui %*
if errorlevel 1 (
    echo.
    echo UI Mirror baslatilamadi. Python 3.10+ ve bagimliliklar gerekli:
    echo   pip install -r "%~dp0requirements.txt"
    echo.
    pause
)
endlocal
