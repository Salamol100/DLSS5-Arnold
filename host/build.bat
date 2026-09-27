@echo off
rem Builds dlss5_host.exe into ..\runtime so ReShade's dxgi.dll next to it is loaded.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
if not exist obj mkdir obj
rem third_party\reshade\include: ReShade 6.8.0 add-on API headers (BSD-3/MIT, crosire/reshade tag v6.8.0)
cl /nologo /O2 /EHsc /std:c++17 /W3 /utf-8 /D_CRT_SECURE_NO_WARNINGS /Ithird_party\reshade\include dlss5_host.cpp /Foobj\ /Fe:..\runtime\dlss5_host.exe
exit /b %errorlevel%
