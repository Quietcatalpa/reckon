"""在资源管理器的“发送到”菜单里添加 / 移除本工具的快捷方式。

    python sendto.py           添加
    python sendto.py --remove  移除
"""
import argparse
import os
import subprocess
import sys

NAME = "盘算 - 整理.lnk"
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
# 路径通过环境变量传给 PowerShell，避免引号和中文转义问题
PS = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SENDTO_LNK);"
      "$s.TargetPath = $env:SENDTO_TARGET; $s.WorkingDirectory = $env:SENDTO_WORKDIR;"
      "$s.Description = $env:SENDTO_DESC; $s.Save()")


def sendto_dir():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "SendTo")


def install(dest_dir=None):
    lnk = os.path.join(dest_dir or sendto_dir(), NAME)
    env = dict(os.environ, SENDTO_LNK=lnk, SENDTO_TARGET=os.path.join(TOOL_DIR, "整理.bat"),
               SENDTO_WORKDIR=TOOL_DIR, SENDTO_DESC="用盘算整理这些文件")
    subprocess.run(["powershell", "-NoProfile", "-Command", PS], env=env, check=True)
    return lnk


def remove(dest_dir=None):
    lnk = os.path.join(dest_dir or sendto_dir(), NAME)
    if os.path.exists(lnk):
        os.remove(lnk)
        return lnk
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--dir", help="快捷方式放在哪里（默认是当前用户的“发送到”文件夹）")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.remove:
        lnk = remove(args.dir)
        print(f"已移除：{lnk}" if lnk else "“发送到”菜单里没有本工具的快捷方式")
    else:
        lnk = install(args.dir)
        print(f"已添加：{lnk}")
        print("现在可以：右键文件或文件夹 → 发送到 → 盘算 - 整理")


if __name__ == "__main__":
    main()
