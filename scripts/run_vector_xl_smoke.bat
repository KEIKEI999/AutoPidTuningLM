@echo off
setlocal

if "%VECTOR_XL_SDK_DIR%"=="" set "VECTOR_XL_SDK_DIR=C:\Users\Public\Documents\Vector\XL Driver Library 20.30.14"
set "PATH=%VECTOR_XL_SDK_DIR%\bin;%PATH%"

"%~dp0..\controller\build\Release\controller.exe"
exit /b %ERRORLEVEL%
