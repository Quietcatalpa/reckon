"""移到回收站之前的检查：确保这一项真的会进回收站，而不是被 Windows 直接永久删除。

Windows 在这些情况下会跳过回收站、直接删除：
- 不是本机硬盘（U 盘、网络盘等没有回收站）；
- 这个盘的回收站被设置成“不将文件移到回收站”；
- 单个文件/文件夹比这个盘回收站的容量上限还大。
Send2Trash 在没装 pywin32 时会带着“不确认”参数调用系统接口，上面几种情况会静默永久删除，
所以这里先检查，放不进回收站的一律拒绝。
"""
import ctypes
import os
import winreg
from ctypes import wintypes

DRIVE_FIXED = 3
BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket"
SAFETY = 0.95  # 留一点余量，不贴着上限放

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_k32.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]


def volume_root(path):
    buf = ctypes.create_unicode_buffer(1024)
    if not _k32.GetVolumePathNameW(os.path.abspath(path), buf, len(buf)):
        raise OSError(ctypes.get_last_error(), "无法确定所在的磁盘")
    return buf.value


def _volume_guid(root):
    buf = ctypes.create_unicode_buffer(1024)
    if not _k32.GetVolumeNameForVolumeMountPointW(root, buf, len(buf)):
        return None
    name = buf.value  # \\?\Volume{xxxx}\
    start, end = name.find("{"), name.find("}")
    return name[start:end + 1] if start >= 0 and end > start else None


def _default_capacity_mb(root):
    """注册表里没有记录时的保守估计：按容量的 5% 算（Windows 的默认值通常更大）。"""
    import shutil
    return shutil.disk_usage(root).total * 0.05 / (1 << 20)


def bin_settings(root):
    """返回 (是否会进回收站, 容量上限字节数)。"""
    guid = _volume_guid(root)
    nuke, cap_mb = 0, None
    if guid:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BITBUCKET + "\\Volume\\" + guid) as k:
                try:
                    nuke = winreg.QueryValueEx(k, "NukeOnDelete")[0]
                except OSError:
                    pass
                try:
                    cap_mb = winreg.QueryValueEx(k, "MaxCapacity")[0]
                except OSError:
                    pass
        except OSError:
            pass
    if cap_mb is None:
        cap_mb = _default_capacity_mb(root)
    return not nuke, int(cap_mb * (1 << 20))


def check(path, size):
    """能安全放进回收站返回 None，否则返回原因。"""
    try:
        root = volume_root(path)
    except OSError as e:
        return str(e)
    if _k32.GetDriveTypeW(root) != DRIVE_FIXED:
        return f"{root} 不是本机硬盘，没有回收站，为安全起见不删除"
    enabled, cap = bin_settings(root)
    if not enabled:
        return f"{root} 的回收站被设置为“直接删除”，为安全起见不删除"
    if size > cap * SAFETY:
        return (f"这一项有 {size / (1 << 30):.1f} GB，超过了 {root} 回收站的容量上限 "
                f"{cap / (1 << 30):.1f} GB，放进去会被直接永久删除，所以跳过了。"
                "确实不要的话请自己手动删除，或在回收站属性里调大上限")
    return None
