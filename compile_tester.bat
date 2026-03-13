@echo off
echo Building windows_tester.exe...

python3 -m PyInstaller --onefile --noconsole windows_tester.py

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed. Make sure it is installed:
    echo   pip install pyinstaller
    pause
    exit /b 1
)

echo.
echo Moving exe to current directory...
move /y dist\windows_tester.exe windows_tester.exe

echo Cleaning up...
rmdir /s /q build
rmdir /s /q dist
del /q windows_tester.spec

echo.
echo Done! windows_tester.exe is ready.
pause
