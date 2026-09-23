"""在资源管理器的“发送到”菜单里添加 / 移除本工具的快捷方式。

    python -m reckon --install-sendto   添加
    python -m reckon --remove-sendto    移除

打包成 exe 时快捷方式指向 exe 本身（加上 --organize 参数）；从源码运行时指向 scripts\\整理.bat。
“发送到”会把选中的文件/文件夹路径追加在参数后面。
"""
import os
import subprocess
import sys

from . import config as C

NAME = "盘算 - 整理.lnk"
# 路径通过环境变量传给 PowerShell，避免引号和中文转义问题
PS = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SENDTO_LNK);"
      "$s.TargetPath = $env:SENDTO_TARGET; $s.Arguments = $env:SENDTO_ARGS; $s.WorkingDirectory = $env:SENDTO_WORKDIR;"
      "$s.Description = $env:SENDTO_DESC; $s.Save()")


def sendto_dir():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "SendTo")


def _target():
    if getattr(sys, "frozen", False):
        return sys.executable, "--organize", C.APP_DIR
    return os.path.join(C.PROJECT_DIR, "scripts", "整理.bat"), "", C.PROJECT_DIR


def install(dest_dir=None):
    lnk = os.path.join(dest_dir or sendto_dir(), NAME)
    target, args, workdir = _target()
    env = dict(os.environ, SENDTO_LNK=lnk, SENDTO_TARGET=target, SENDTO_ARGS=args,
               SENDTO_WORKDIR=workdir, SENDTO_DESC="用盘算整理这些文件")
    subprocess.run(["powershell", "-NoProfile", "-Command", PS], env=env, check=True)
    return lnk


def remove(dest_dir=None):
    lnk = os.path.join(dest_dir or sendto_dir(), NAME)
    if os.path.exists(lnk):
        os.remove(lnk)
        return lnk
    return None
