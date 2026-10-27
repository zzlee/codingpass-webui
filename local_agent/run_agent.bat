@echo off
rem CodingPass Local Agent Launcher (Windows)
cd /d "%~dp0"
echo ==================================================
echo   CodingPass Local Agent (學生端本地編譯代理)
echo ==================================================

rem 1. Check if bundled w64devkit or MinGW exists in current directory or common paths
if exist "%~dp0w64devkit\bin\gcc.exe" (
    set "PATH=%~dp0w64devkit\bin;%PATH%"
    echo [OK] Using bundled w64devkit GCC compiler.
) else if exist "%~dp0mingw64\bin\gcc.exe" (
    set "PATH=%~dp0mingw64\bin;%PATH%"
    echo [OK] Using bundled MinGW64 compiler.
) else if exist "C:\msys64\mingw64\bin\gcc.exe" (
    set "PATH=C:\msys64\mingw64\bin;%PATH%"
    echo [OK] Using system MSYS2 MinGW64 compiler.
) else if exist "C:\MinGW\bin\gcc.exe" (
    set "PATH=C:\MinGW\bin;%PATH%"
    echo [OK] Using system MinGW compiler.
)

rem 2. Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python 3 not found! Please install Python or extract Python embeddable.
        pause
        exit /b 1
    ) else (
        py -3 agent.py
    )
) else (
    python agent.py
)

pause
