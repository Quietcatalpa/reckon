@echo off
chcp 65001 >nul
rem 在“发送到”菜单里加一个指向 整理.bat 的快捷方式。想去掉的话运行：python sendto.py --remove
cd /d "%~dp0"
python sendto.py %*
pause
