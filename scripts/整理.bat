@echo off
chcp 65001 >nul
rem 把文件或文件夹拖到这个文件上，或通过右键“发送到”调用：打开整理页面并填好这些路径
cd /d "%~dp0.."
python -m reckon --organize %*
if errorlevel 1 pause
