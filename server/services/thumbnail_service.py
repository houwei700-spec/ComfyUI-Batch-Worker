import os
import threading
import tempfile
from PIL import Image
from server.config import ASSETS_DIR, log_msg
from server.services.path_service import valid_asset_name, resolve_under

# 解除大像素限制防护（因为单反服装拍摄原图可能超过 1 亿像素）
Image.MAX_IMAGE_PIXELS = None

THUMBS_DIR = os.path.join(ASSETS_DIR, ".thumbs_cache")
THUMB_SIZE = (320, 320)

def get_thumbnail_path(category, filename):
    """获取缩略图文件路径，若不存在则即时生成并缓存"""
    if not valid_asset_name(category, filename):
        return None
    cat_thumb_dir = os.path.join(THUMBS_DIR, category)
    os.makedirs(cat_thumb_dir, exist_ok=True)
    
    thumb_filename = f"{filename}.thumb.jpg"
    thumb_path = os.path.join(cat_thumb_dir, thumb_filename)
    
    orig_path = resolve_under(ASSETS_DIR, f"{category}/{filename}")
    if not orig_path or not os.path.isfile(orig_path):
        return None
        
    # 如果缩略图已存在且修改时间晚于原图，直接返回缓存
    if os.path.exists(thumb_path):
        try:
            if os.path.getmtime(thumb_path) >= os.path.getmtime(orig_path):
                return thumb_path
        except Exception:
            return thumb_path

    # 生成缩略图
    tmp_path = None
    try:
        with Image.open(orig_path) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
            with tempfile.NamedTemporaryFile(dir=cat_thumb_dir, delete=False) as tmp:
                tmp_path = tmp.name
            im.save(tmp_path, "JPEG", quality=85, optimize=True)
            os.replace(tmp_path, thumb_path)
            return thumb_path
    except Exception as e:
        log_msg(f"[!] 缩略图生成失败 ({category}/{filename}): {e}")
        return orig_path
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

def prebuild_all_thumbnails():
    """在后台静默预生成所有资产缩略图，确保用户点击时 100% 命中缓存"""
    categories = ["models", "apparels", "scenes"]
    count = 0
    for cat in categories:
        cat_dir = os.path.join(ASSETS_DIR, cat)
        if not os.path.exists(cat_dir):
            continue
        for f in os.listdir(cat_dir):
            ext = os.path.splitext(f)[1].lower()
            if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                try:
                    get_thumbnail_path(cat, f)
                    count += 1
                except Exception:
                    pass
    log_msg(f"[*] 缩略图极速缓存引擎已预热完成，就绪资产: {count} 件")

def start_thumbnail_prebuild_worker():
    """启动后台守护线程进行静默预热"""
    t = threading.Thread(target=prebuild_all_thumbnails, daemon=True, name="ThumbPrebuildWorker")
    t.start()
