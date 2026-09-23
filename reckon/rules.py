"""个人规则：永远保留的文件夹、以后不再提示的项、整理时各类文件的默认去处、判断阈值。

存在 %LOCALAPPDATA%\\Reckon\\rules.json，页面上的「设置」里可以改。
"""
import json
import os

from . import config as C
from . import decide
from .config import norm, under

DEFAULT_SETTINGS = {"delete_at": 0.70, "review_at": 0.40, "w_rule": 0.45}
LIMITS = {"delete_at": (0.5, 0.95), "review_at": (0.1, 0.9), "w_rule": (0.0, 1.0)}
TYPES = list(C.TYPE_EXTS) + ["其他"]


def defaults():
    return {"keep_dirs": [], "ignored": [], "type_dest": {}, "settings": dict(DEFAULT_SETTINGS)}


def load():
    r = defaults()
    try:
        with open(C.RULES_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        clean, _ = validate(saved)
        r.update(clean)
    except (OSError, ValueError):
        pass
    return r


def save(r):
    os.makedirs(os.path.dirname(C.RULES_FILE), exist_ok=True)
    tmp = C.RULES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, C.RULES_FILE)


def _abs_dir(p):
    p = (p or "").strip().strip('"')
    return os.path.normpath(p) if p and os.path.isabs(p) else None


def validate(r):
    """整理成合法的规则，返回 (规则, 错误列表)。不合法的项丢掉并说明原因。"""
    out, errors = defaults(), []
    for p in r.get("keep_dirs", []):
        d = _abs_dir(p)
        if not d:
            errors.append(f"“{p}”不是完整的文件夹路径")
        elif d not in out["keep_dirs"]:
            out["keep_dirs"].append(d)
    seen = set()
    for e in r.get("ignored", []):
        if isinstance(e, dict) and e.get("key") and e["key"] not in seen:
            seen.add(e["key"])
            out["ignored"].append({"key": e["key"], "name": str(e.get("name", "")), "path": str(e.get("path", ""))})
    for t, p in (r.get("type_dest") or {}).items():
        d = _abs_dir(p)
        if t not in TYPES:
            errors.append(f"没有“{t}”这个文件类型")
        elif not d:
            errors.append(f"“{t}”的去处“{p}”不是完整的文件夹路径")
        elif C.is_protected(norm(d)):
            errors.append(f"“{d}”是受保护的位置，不能作为去处")
        else:
            out["type_dest"][t] = d
    s = r.get("settings") or {}
    for k, (lo, hi) in LIMITS.items():
        try:
            v = float(s.get(k, DEFAULT_SETTINGS[k]))
        except (TypeError, ValueError):
            v = DEFAULT_SETTINGS[k]
            errors.append(f"{k} 不是数字，已用默认值")
        if not lo <= v <= hi:
            errors.append(f"{k} 要在 {lo}–{hi} 之间，已用默认值")
            v = DEFAULT_SETTINGS[k]
        out["settings"][k] = round(v, 3)
    if out["settings"]["review_at"] >= out["settings"]["delete_at"]:
        errors.append("“需要你看”的下限要低于“建议删除”的线，已恢复默认")
        out["settings"]["review_at"], out["settings"]["delete_at"] = DEFAULT_SETTINGS["review_at"], DEFAULT_SETTINGS["delete_at"]
    return out, errors


def apply_settings(r):
    """把阈值和权重应用到判断逻辑上（下次扫描生效；当前结果的分栏在页面上按新阈值重新计算）。"""
    s = r["settings"]
    decide.DELETE_AT, decide.REVIEW_AT, decide.W_RULE = s["delete_at"], s["review_at"], s["w_rule"]


def item_key(it):
    """忽略一项时用的标识：缓存目录按名称（每次扫描路径可能不同），文件按路径。"""
    return "group:" + it["name"] if it["kind"] == "group" else "path:" + norm(it["path"])


def kept_dir(path, r):
    """path 在某个“永远保留”的文件夹里就返回那个文件夹。"""
    np_ = norm(path)
    return next((d for d in r["keep_dirs"] if under(np_, norm(d))), None)


def hidden_reason(it, r, ignored=None):
    """这一项按规则不该出现在清理建议里的原因；可以出现返回 None。"""
    ignored = ignored if ignored is not None else {e["key"] for e in r["ignored"]}
    if item_key(it) in ignored:
        return "你设置了以后不再提示这一项"
    for p in it.get("paths") or [it["path"]]:
        d = kept_dir(p, r)
        if d:
            return f"在你设置的“永远保留”文件夹「{d}」里"
    return None
