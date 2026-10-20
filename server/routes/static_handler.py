import os
import mimetypes
import subprocess
import urllib.parse
import json
from server.config import WEB_DIR, ASSETS_DIR, OUTPUT_DIR, log_msg
from server.services.thumbnail_service import get_thumbnail_path
from server.services.path_service import resolve_under, valid_asset_name

def serve_static_file(handler, filepath):
    if not os.path.exists(filepath) or os.path.isdir(filepath):
        handler.send_response(404)
        handler.end_headers()
        handler.wfile.write(b"404 Not Found")
        return

    content_type, _ = mimetypes.guess_type(filepath)
    if filepath.endswith(".js"):
        content_type = "application/javascript; charset=utf-8"
    elif filepath.endswith(".css"):
        content_type = "text/css; charset=utf-8"
    elif filepath.endswith(".html"):
        content_type = "text/html; charset=utf-8"
    elif not content_type:
        content_type = "application/octet-stream"

    try:
        with open(filepath, "rb") as f:
            content = f.read()
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(content)))
        if filepath.endswith(".js") or filepath.endswith(".css") or filepath.endswith(".html"):
            handler.send_header("Cache-Control", "no-cache, must-revalidate")
        else:
            handler.send_header("Cache-Control", "public, max-age=3600")
        handler.end_headers()
        handler.wfile.write(content)
    except Exception as e:
        log_msg(f"[!] 托管静态文件异常: {e}")
        handler.send_response(500)
        handler.end_headers()

def serve_thumbnail_image(handler, path):
    parts = path.strip("/").split("/")
    if len(parts) == 5:
        category = parts[3]
        filename = urllib.parse.unquote(parts[4])
        
        thumb_path = get_thumbnail_path(category, filename)
        if thumb_path and os.path.exists(thumb_path):
            try:
                mtime = str(os.stat(thumb_path).st_mtime_ns)
                client_etag = handler.headers.get("If-None-Match", "")
                if client_etag == f'"{mtime}"':
                    handler.send_response(304)
                    handler.end_headers()
                    return True

                with open(thumb_path, "rb") as f:
                    content = f.read()
                handler.send_response(200)
                handler.send_header("Content-Type", mimetypes.guess_type(thumb_path)[0] or "image/jpeg")
                handler.send_header("Content-Length", str(len(content)))
                handler.send_header("Cache-Control", "no-cache")
                handler.send_header("ETag", f'"{mtime}"')
                handler.end_headers()
                handler.wfile.write(content)
                return True
            except Exception as e:
                log_msg(f"[!] 传输缩略图异常: {e}")
    return False

def serve_asset_image(handler, path):
    parts = path.strip("/").split("/")
    if len(parts) == 4:
        category = parts[2]
        filename = urllib.parse.unquote(parts[3])
        if not valid_asset_name(category, filename):
            return False
        file_path = resolve_under(ASSETS_DIR, f"{category}/{filename}")

        if file_path and os.path.isfile(file_path):
            try:
                mime, _ = mimetypes.guess_type(file_path)
                with open(file_path, "rb") as f:
                    content = f.read()
                handler.send_response(200)
                handler.send_header("Content-Type", mime or "image/jpeg")
                handler.send_header("Content-Length", str(len(content)))
                handler.send_header("Cache-Control", "public, max-age=86400")
                handler.end_headers()
                handler.wfile.write(content)
                return True
            except Exception as e:
                log_msg(f"[!] 传输原图异常: {e}")
    return False

def _safe_resolve_output_path(fn):
    """安全解析 output 目录中的文件路径，兼容旧平铺文件与新分层目录，防止路径穿越"""
    if not fn:
        return os.path.normpath(OUTPUT_DIR)
    norm_output = os.path.normpath(OUTPUT_DIR)
    
    # 统一路径分隔符
    clean_fn = fn.replace("/", os.sep).replace("\\", os.sep)
    full_path = resolve_under(norm_output, clean_fn)
    
    # 安全检查：防止路径穿越超出 OUTPUT_DIR
    if not full_path:
        return None
        
    if os.path.exists(full_path):
        return full_path
        
    # 向后兼容：如果在多级目录中找不到，检查是否为历史老平铺文件
    flat_path = resolve_under(norm_output, os.path.basename(clean_fn))
    if flat_path and os.path.exists(flat_path):
        return flat_path
        
    return full_path

def serve_video_stream(handler, query_params):
    fn = query_params.get("filename", [""])[0]
    full_path = _safe_resolve_output_path(fn)
    if full_path and os.path.exists(full_path) and os.path.isfile(full_path):
        file_size = os.path.getsize(full_path)
        range_header = handler.headers.get("Range")
        start, end = 0, file_size - 1
        if range_header:
            try:
                unit, byte_range = range_header.strip().split("=", 1)
                if unit != "bytes" or "," in byte_range:
                    raise ValueError("Unsupported range")
                start_str, end_str = byte_range.split("-", 1)
                if start_str:
                    start = int(start_str)
                    end = min(int(end_str), file_size - 1) if end_str else file_size - 1
                else:
                    suffix = int(end_str)
                    if suffix <= 0:
                        raise ValueError("Invalid suffix")
                    start = max(0, file_size - suffix)
                if start < 0 or start >= file_size or end < start:
                    raise ValueError("Unsatisfiable range")
            except (ValueError, TypeError):
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{file_size}")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return True

        length = max(0, end - start + 1)
        handler.send_response(206 if range_header else 200)
        handler.send_header("Content-Type", mimetypes.guess_type(full_path)[0] or "video/mp4")
        if range_header:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        handler.send_header("Content-Length", str(length))
        handler.send_header("Accept-Ranges", "bytes")
        handler.end_headers()
        with open(full_path, "rb") as f:
            f.seek(start)
            while length:
                chunk = f.read(min(length, 1024 * 1024))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                length -= len(chunk)
        return True
    return False

def _create_desktop_startupinfo():
    """构建直接指向用户物理交互桌面 (WinSta0\\Default) 的启动信息"""
    si = subprocess.STARTUPINFO()
    si.lpDesktop = r"WinSta0\Default"
    return si

def handle_open_output(handler):
    norm_dir = os.path.normpath(OUTPUT_DIR)
    os.makedirs(norm_dir, exist_ok=True)
    
    try:
        si = _create_desktop_startupinfo()
        subprocess.Popen(["explorer.exe", norm_dir], startupinfo=si)
        log_msg(f"[*] 已在用户桌面唤起资源管理器打开目录: {norm_dir}")
    except Exception as e:
        log_msg(f"[!] 唤起目录失败: {e}")
        try:
            os.startfile(norm_dir)
        except Exception:
            pass

    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(f'{{"success":true,"path":"{norm_dir.replace("\\", "/")}"}}'.encode("utf-8"))

def handle_locate_file(handler, query_params):
    fn = query_params.get("filename", [""])[0]
    full_path = _safe_resolve_output_path(fn)
    if not full_path:
        handler.send_response(400)
        handler.end_headers()
        return
    
    try:
        si = _create_desktop_startupinfo()
        if os.path.exists(full_path):
            # 关键：直接在用户当前主屏幕桌面上高亮选中该文件
            subprocess.Popen(["explorer.exe", f"/select,{full_path}"], startupinfo=si)
            log_msg(f"[*] 已在用户桌面高亮定位文件: {full_path}")
        else:
            norm_dir = os.path.normpath(OUTPUT_DIR)
            subprocess.Popen(["explorer.exe", norm_dir], startupinfo=si)
            log_msg(f"[!] 定位目标文件不存在: {full_path}，已打开父目录")
    except Exception as e:
        log_msg(f"[!] 定位文件失败: {e}")

    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps({"success": True, "file": fn, "path": full_path.replace("\\", "/")}, ensure_ascii=False).encode("utf-8"))
