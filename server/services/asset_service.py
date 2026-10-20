import os
import urllib.request
import urllib.parse
from server.config import ASSETS_DIR, log_msg

def get_dir_images(dir_path):
    if not os.path.exists(dir_path):
        return []
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = []
    for f in sorted(os.listdir(dir_path)):
        ext = os.path.splitext(f)[1].lower()
        if ext in valid_exts:
            files.append(f)
    return files


def resolve_cloud_image_path(server, category, filename):
    if not filename:
        return ""
    clean_fn = os.path.basename(filename)

    candidate_1 = f"{category}/{clean_fn}"
    try:
        url1 = f"{server}/view?filename={urllib.parse.quote(clean_fn)}&subfolder={urllib.parse.quote(category)}&type=input"
        req = urllib.request.Request(url1, headers={"User-Agent": "HubResolver"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return candidate_1
    except Exception:
        pass

    candidate_2 = clean_fn
    try:
        url2 = f"{server}/view?filename={urllib.parse.quote(clean_fn)}&type=input"
        req = urllib.request.Request(url2, headers={"User-Agent": "HubResolver"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return candidate_2
    except Exception:
        pass

    return candidate_1

