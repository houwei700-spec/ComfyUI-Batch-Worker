import os
import sys
import json
import threading
import time

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SERVER_DIR)
WEB_DIR = os.path.join(BASE_DIR, "web")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
MODELS_DIR = os.path.join(ASSETS_DIR, "models")
APPARELS_DIR = os.path.join(ASSETS_DIR, "apparels")
SCENES_DIR = os.path.join(ASSETS_DIR, "scenes")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TASK_FILE = os.path.join(BASE_DIR, "tasks.csv")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "batch_hub.log")
PORT = 8999

for d in [TEMPLATES_DIR, MODELS_DIR, APPARELS_DIR, SCENES_DIR, OUTPUT_DIR, WEB_DIR]:
    os.makedirs(d, exist_ok=True)

def log_msg(msg):
    t_str = time.strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{t_str}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


config_lock = threading.Lock()
tasks_lock = threading.Lock()
batch_master_lock = threading.Lock()
batch_stop_event = threading.Event()
batch_is_active = False
worker_status_map = {}

def load_server_config():
    """读取并自动平滑升级配置"""
    with config_lock:
        cfg = {
            "servers": [
                {
                    "id": "srv_1",
                    "name": "本机 ComfyUI",
                    "url": "http://127.0.0.1:8188",
                    "enabled": True
                }
            ],
            "active_server_id": "srv_1",
            "workflow_template_path": "template_workflow.json",
            "task_file": "tasks.csv",
            "poll_interval_seconds": 5,
            "max_retries": 2
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    disk_cfg = json.load(f)
                
                raw_servers = disk_cfg.get("servers", [])
                norm_servers = []
                for idx, s in enumerate(raw_servers):
                    if isinstance(s, str):
                        norm_servers.append({
                            "id": f"srv_{idx+1}",
                            "name": f"算力节点-{idx+1}",
                            "url": s.rstrip("/"),
                            "enabled": True
                        })
                    elif isinstance(s, dict):
                        if "id" not in s:
                            s["id"] = f"srv_{idx+1}"
                        if "name" not in s:
                            s["name"] = f"算力节点-{idx+1}"
                        if "url" in s:
                            s["url"] = s["url"].rstrip("/")
                        if "enabled" not in s:
                            s["enabled"] = True
                        norm_servers.append(s)
                
                if norm_servers:
                    cfg["servers"] = norm_servers
                
                active_id = disk_cfg.get("active_server_id")
                valid_ids = [s["id"] for s in cfg["servers"]]
                if active_id in valid_ids:
                    cfg["active_server_id"] = active_id
                else:
                    cfg["active_server_id"] = valid_ids[0] if valid_ids else "srv_1"
                
                for k in ["workflow_template_path", "task_file", "poll_interval_seconds", "max_retries"]:
                    if k in disk_cfg:
                        cfg[k] = disk_cfg[k]
            except Exception as e:
                log_msg(f"[!] 读取 config.json 异常: {e}")
        return cfg


def save_server_config(cfg):
    """原子写入配置文件，避免崩溃损坏"""
    with config_lock:
        tmp_file = CONFIG_FILE + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(tmp_file, CONFIG_FILE)
            return True
        except Exception as e:
            log_msg(f"[-] 保存配置失败: {e}")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            return False


def get_active_server(cfg=None):
    """获取当前处于激活且已启用的主控服务器"""
    if cfg is None:
        cfg = load_server_config()
    active_id = cfg.get("active_server_id", "")
    servers = cfg.get("servers", [])
    
    for s in servers:
        if s.get("id") == active_id and s.get("enabled", True):
            return s
            
    for s in servers:
        if s.get("enabled", True):
            return s
            
    return servers[0] if servers else None


