@echo off
chcp 65001 >nul
rem 在“发送到”菜单里加一个快捷方式。想去掉的话运行：python -m reckon --remove-sendto
cd /d "%~dp0.."
python -m reckon --install-sendto
pause
