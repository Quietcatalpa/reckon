"""路径与类型规则：哪些目录绝不碰、哪些目录算缓存、文件怎么分类。"""
import os
import winreg

HOME = os.path.expanduser("~")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.path.join(HOME, "AppData", "Local"))
APPDATA = os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming"))
TEMP = os.path.join(LOCALAPPDATA, "Temp")
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(TOOL_DIR, "results", "last_scan.json")
LAYA_CACHE_FILE = os.path.join(TOOL_DIR, "results", "laya_cache.json")  # 记住每个文件的模型判断
HF_HUB = os.path.join(os.environ.get("HF_HOME", os.path.join(HOME, ".cache", "huggingface")), "hub")
LAYA_REPO = os.path.join(HF_HUB, "models--convaiinnovations--laya-multilingual")
HOME_CACHE = os.path.join(HOME, ".cache")


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def under(np_, base_n):
    """np_ 是否等于 base_n 或位于其下（两者都已 norm）。"""
    return np_ == base_n or np_.startswith(base_n.rstrip("\\") + "\\")


def under_any(np_, bases):
    return any(under(np_, b) for b in bases)


def _shell_folder(value_name, fallback):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            v, _ = winreg.QueryValueEx(k, value_name)
            return os.path.expandvars(v)
    except OSError:
        return fallback


DESKTOP = _shell_folder("Desktop", os.path.join(HOME, "Desktop"))
DOCUMENTS = _shell_folder("Personal", os.path.join(HOME, "Documents"))
DOWNLOADS = _shell_folder("{374DE290-123F-4565-9164-39C4925E467B}", os.path.join(HOME, "Downloads"))
PICTURES = _shell_folder("My Pictures", os.path.join(HOME, "Pictures"))
VIDEOS = _shell_folder("My Video", os.path.join(HOME, "Videos"))

TEMP_N = norm(TEMP)
DOWNLOADS_N = norm(DOWNLOADS)
APPDATA_NS = [norm(LOCALAPPDATA), norm(APPDATA), norm(os.path.join(HOME, "AppData", "LocalLow"))]
PERSONAL_NS = [norm(p) for p in (DESKTOP, DOCUMENTS, PICTURES, VIDEOS)]

# ---- 绝不扫描、绝不删除 ----
# 每个盘根目录下的这些文件夹直接跳过
ROOT_SKIP_DIRS = {
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
    "system volume information", "recovery", "$winreagent", "config.msi", "boot",
    "$windows.~bt", "$windows.~ws", "$sysreset", "$getcurrent", "msocache", "windowsapps",
    "onedrivetemp", "esd", "wpsystem",
}
ROOT_SKIP_FILES = {
    "pagefile.sys", "hiberfil.sys", "swapfile.sys", "dumpstack.log", "dumpstack.log.tmp",
    "bootmgr", "bootnxt",
}
PROTECTED_NS = [norm(p) for p in (
    os.path.join(APPDATA, "Claude"),
    os.path.join(APPDATA, "Microsoft"),
    os.path.join(LOCALAPPDATA, "AnthropicClaude"),
    os.path.join(LOCALAPPDATA, "Packages"),
    os.path.join(LOCALAPPDATA, "Microsoft", "WindowsApps"),
    os.path.join(TEMP, "claude"),
    os.path.join(HOME, ".claude"),
    TOOL_DIR,
    LAYA_REPO,
)]
# 目录里出现这些文件/文件夹，说明是已安装的程序或 conda 环境，整个跳过
UNINSTALL_MARKERS = {"unins000.exe", "unins001.exe", "uninstall.exe", "uninst.exe", "uninstaller.exe", "卸载.exe"}
SKIP_DIR_NAMES = {".git", ".svn", ".hg", "steamapps"}
# 程序组件：删了软件会坏，不当作重复文件，也不建议删除（下载文件夹除外）
PROGRAM_EXTS = set(".exe .dll .pyd .so .sys .ocx .node .jar .asar .pak .lib .a .drv .mui".split())
# 聊天软件接收文件的目录：是用户资料，最多到“需要你看”
CHAT_DIR_NAMES = {"xwechat_files", "wechat files", "tencent files", "wxwork", "wxwork files", "wecom files",
                  "dingtalk", "feishu"}


def is_protected(np_):
    if under_any(np_, PROTECTED_NS):
        return True
    parts = np_.split("\\")
    # parts: ['d:', 'windows', ...]
    return len(parts) >= 2 and parts[1] in ROOT_SKIP_DIRS


# ---- 缓存目录：整块作为一项，由规则判断 ----
# 精确路径 -> (名称, 删除把握, 说明)
KNOWN_GROUPS = {norm(k): v for k, v in {
    os.path.join(LOCALAPPDATA, "pip", "cache"): ("pip 下载缓存", 0.92, "pip 安装包缓存，需要时会重新下载"),
    os.path.join(HOME_CACHE, "pip"): ("pip 下载缓存", 0.92, "pip 安装包缓存，需要时会重新下载"),
    os.path.join(LOCALAPPDATA, "npm-cache"): ("npm 缓存", 0.9, "npm 包缓存，需要时会重新下载"),
    os.path.join(LOCALAPPDATA, "Yarn", "Cache"): ("Yarn 缓存", 0.9, "Yarn 包缓存，需要时会重新下载"),
    os.path.join(LOCALAPPDATA, "CrashDumps"): ("程序崩溃转储", 0.95, "程序崩溃时留下的转储文件"),
    os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "INetCache"): ("系统网页缓存", 0.9, "IE/系统组件的网页缓存"),
    os.path.join(LOCALAPPDATA, "Microsoft", "Windows", "WER"): ("Windows 错误报告", 0.9, "已发送或过期的错误报告"),
    os.path.join(LOCALAPPDATA, "D3DSCache"): ("DirectX 着色器缓存", 0.9, "显卡着色器缓存，会自动重建"),
    os.path.join(LOCALAPPDATA, "NVIDIA", "DXCache"): ("NVIDIA 着色器缓存", 0.9, "显卡着色器缓存，会自动重建"),
    os.path.join(LOCALAPPDATA, "NVIDIA", "GLCache"): ("NVIDIA 着色器缓存", 0.9, "显卡着色器缓存，会自动重建"),
    os.path.join(HOME, ".gradle", "caches"): ("Gradle 缓存", 0.75, "Gradle 依赖缓存，构建时会重新下载"),
    os.path.join(HOME, ".m2", "repository"): ("Maven 本地仓库", 0.55, "Maven 依赖，构建时会重新下载"),
    os.path.join(HOME, ".nuget", "packages"): ("NuGet 包缓存", 0.6, "NuGet 依赖，构建时会重新下载"),
}.items()}
# AppData 下名字是这些的目录，视为应用缓存
CACHE_DIR_NAMES = {
    "cache", "code cache", "gpucache", "dawncache", "dawnwebgpucache", "dawngraphitecache",
    "grshadercache", "graphitedawncache", "shadercache", "cache_data", "cachestorage",
    "scriptcache", "cache2", "cacheddata", "cachedextensionvsixs", "cachedprofilesdata",
    "crashpad", "crashreports", "logs",
}
TEMP_MIN_AGE_DAYS = 3  # 临时目录里 3 天内动过的不算

# ---- 文件类型 ----
TYPE_EXTS = {
    "文档": ".doc .docx .pdf .ppt .pptx .xls .xlsx .xlsm .txt .md .rtf .odt .wps .et .dps .tex .caj .epub .mobi",
    "代码": ".py .ipynb .js .ts .jsx .tsx .java .c .cpp .h .hpp .m .r .go .rs .cs .html .css .sql .sh .bat .ps1 .json .xml .yaml .yml .ini .cfg .toml",
    "数据": ".csv .tsv .parquet .feather .h5 .hdf5 .npy .npz .mat .pkl .pickle .pt .pth .ckpt .safetensors .onnx .bin .db .sqlite .dta .sav",
    "备份": ".bak .old .backup .bk .orig",
    "日志缓存": ".log .tmp .temp .dmp .mdmp .cache .crdownload .part .partial .etl .download "
            ".qkdownloading .downloading .td .xltd .bc! .aria2 .!ut",
    "安装包": ".exe .msi .msix .appx .apk .dmg .iso .img .xapk",
    "压缩包": ".zip .rar .7z .tar .gz .tgz .bz2 .xz",
    "图片": ".jpg .jpeg .png .gif .bmp .webp .heic .tif .tiff .raw .cr2 .nef .psd .ai .svg",
    "视频": ".mp4 .mkv .avi .mov .wmv .flv .webm .m4v .rmvb .3gp",
    "音频": ".mp3 .wav .flac .aac .m4a .ogg .wma .amr",
}
EXT_TYPE = {}
for _t, _exts in TYPE_EXTS.items():
    for _e in _exts.split():
        EXT_TYPE.setdefault(_e, _t)
MEDIA_TYPES = {"图片", "视频", "音频"}
# 安装包和图片视频只按时间判断，不送模型
NO_MODEL_TYPES = {"安装包"} | MEDIA_TYPES
# 没下完的下载、崩溃转储：一定是垃圾，只按规则判断，不送模型（模型对这类文件没有帮助，还会把分数往下拉）
SURE_JUNK_EXTS = set(".crdownload .part .partial .download .qkdownloading .downloading .td .xltd .bc! .aria2 .!ut "
                     ".dmp .mdmp".split())
INSTALLER_NAME_HINTS = ("setup", "install", "安装", "installer", "_x64", "-x64", "win64", "win32")


def file_type(ext):
    return EXT_TYPE.get(ext, "其他")


def in_chat_dir(np_):
    return any(seg in CHAT_DIR_NAMES for seg in np_.split("\\"))
