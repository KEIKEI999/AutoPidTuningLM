@echo off
setlocal
set SCRIPT_DIR=%~dp0
set VS_DIR=%SCRIPT_DIR%..\controller\vs2017
set CONFIGURATION=%~1
set PLATFORM=%~2
set CAN_ADAPTER=%~3

if "%CONFIGURATION%"=="" set CONFIGURATION=Release
if "%PLATFORM%"=="" set PLATFORM=Win32
if "%CAN_ADAPTER%"=="" set CAN_ADAPTER=stub

set MSBUILD_PATH=
if exist "C:\Program Files (x86)\Microsoft Visual Studio\2017\WDExpress\MSBuild\15.0\Bin\MSBuild.exe" set MSBUILD_PATH=C:\Program Files (x86)\Microsoft Visual Studio\2017\WDExpress\MSBuild\15.0\Bin\MSBuild.exe
if "%MSBUILD_PATH%"=="" if exist "C:\Program Files (x86)\Microsoft Visual Studio\2017\BuildTools\MSBuild\15.0\Bin\MSBuild.exe" set MSBUILD_PATH=C:\Program Files (x86)\Microsoft Visual Studio\2017\BuildTools\MSBuild\15.0\Bin\MSBuild.exe
if "%MSBUILD_PATH%"=="" (
  echo MSBuild.exe was not found.
  exit /b 1
)

pushd "%VS_DIR%"
"%MSBUILD_PATH%" controller.sln /t:Build /p:Configuration=%CONFIGURATION% /p:Platform=%PLATFORM% /p:CanAdapter=%CAN_ADAPTER% /m /nologo
set BUILD_RC=%ERRORLEVEL%
popd
exit /b %BUILD_RC%
