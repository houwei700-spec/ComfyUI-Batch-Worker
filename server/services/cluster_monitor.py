import time
import threading
from server.config import load_server_config, worker_status_map, log_msg
from server.services.cloud_service import probe_all_servers_concurrently, probe_single_server

_cached_cluster_data = {
    "servers": [],
    "active_server_id": "",
    "last_updated": 0
}
_lock = threading.Lock()
_monitor_thread_started = False

def update_cluster_cache():
    global _cached_cluster_data
    try:
        cfg = load_server_config()
        servers = cfg.get("servers", [])
        active_id = cfg.get("active_server_id", "")
        probe_results = probe_all_servers_concurrently(servers, timeout=3)
        
        output_servers = []
        for s in servers:
            item = dict(s)
            item["status"] = probe_results.get(s["id"], {"online": False, "latency_ms": 0, "gpu_info": "离线"})
            item["worker_status"] = worker_status_map.get(s["id"], {"state": "IDLE", "task_id": ""})
            output_servers.append(item)
            
        with _lock:
            _cached_cluster_data = {
                "servers": output_servers,
                "active_server_id": active_id,
                "last_updated": time.time()
            }
    except Exception as e:
        log_msg(f"[!] 集群后台探活异常: {e}")

def get_cached_cluster_data():
    with _lock:
        return dict(_cached_cluster_data)

def _cluster_monitor_loop():
    while True:
        update_cluster_cache()
        time.sleep(4)

def start_cluster_monitor():
    global _monitor_thread_started
    if not _monitor_thread_started:
        _monitor_thread_started = True
        # 先立刻执行一次初始化
        t0 = threading.Thread(target=update_cluster_cache, daemon=True)
        t0.start()
        # 启动后台守护循环
        t = threading.Thread(target=_cluster_monitor_loop, daemon=True, name="ClusterMonitorDaemon")
        t.start()
        log_msg("[*] 算力集群后台异步监测守护线程已启动 (0 阻塞秒开)")
