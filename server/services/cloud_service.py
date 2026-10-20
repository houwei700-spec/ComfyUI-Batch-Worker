import time
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from server.config import log_msg

def probe_single_server(server_dict, timeout=4):
    """对单个端口进行健康探测"""
    url = server_dict.get("url", "").rstrip("/")
    if not url:
        return {"online": False, "latency_ms": 0, "gpu_info": "未配置地址", "queue_running": 0, "queue_pending": 0}
    
    start_time = time.time()
    try:
        req = urllib.request.Request(f"{url}/system_stats", headers={"User-Agent": "HubProbe"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                elapsed_ms = int((time.time() - start_time) * 1000)
                data = json.loads(resp.read().decode("utf-8"))
                
                devices = data.get("devices", [])
                gpu_names = []
                for d in devices:
                    n = d.get("name", "")
                    if "NVIDIA" in n:
                        parts = n.split(" : ")[0].replace("cuda:0 ", "").replace("cuda:1 ", "").strip()
                        gpu_names.append(parts)
                    elif n:
                        gpu_names.append(n)
                
                if gpu_names:
                    gpu_str = f"{len(gpu_names)}x {gpu_names[0]}" if len(set(gpu_names)) == 1 else ", ".join(gpu_names[:2])
                else:
                    gpu_str = "ComfyUI 在线就绪"
                
                q_running = 0
                q_pending = 0
                try:
                    q_req = urllib.request.Request(f"{url}/queue", headers={"User-Agent": "HubProbe"})
                    with urllib.request.urlopen(q_req, timeout=2) as q_resp:
                        q_data = json.loads(q_resp.read().decode("utf-8"))
                        q_running = len(q_data.get("queue_running", []))
                        q_pending = len(q_data.get("queue_pending", []))
                except Exception:
                    pass

                return {
                    "online": True,
                    "latency_ms": elapsed_ms,
                    "gpu_info": gpu_str,
                    "queue_running": q_running,
                    "queue_pending": q_pending
                }
    except Exception:
        pass

    return {
        "online": False,
        "latency_ms": 0,
        "gpu_info": "连接超时或离线",
        "queue_running": 0,
        "queue_pending": 0
    }


def probe_all_servers_concurrently(servers, timeout=4):
    """多线程并发探测所有节点，无阻塞保障快速响应"""
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(servers) or 1, 64)) as executor:
        future_map = {executor.submit(probe_single_server, s, timeout): s["id"] for s in servers}
        for future in future_map:
            sid = future_map[future]
            try:
                results[sid] = future.result()
            except Exception:
                results[sid] = {"online": False, "latency_ms": 0, "gpu_info": "检测异常", "queue_running": 0, "queue_pending": 0}
    return results

