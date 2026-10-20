import os


def resolve_under(root, relative):
    """返回目录内部的规范路径；拒绝绝对路径、盘符及目录穿越。"""
    if not isinstance(relative, str) or "\x00" in relative:
        return None
    relative = relative.replace("\\", "/")
    if relative.startswith("/") or ":" in relative:
        return None
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, relative))
    try:
        if os.path.normcase(os.path.commonpath([base, target])) != os.path.normcase(base):
            return None
    except ValueError:
        return None
    return target


def valid_asset_name(category, filename):
    return (category in {"models", "apparels", "scenes"}
            and isinstance(filename, str) and bool(filename)
            and filename not in {".", ".."}
            and not any(c in filename for c in '/\\:\x00'))
