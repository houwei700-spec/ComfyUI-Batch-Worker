import threading
import time
from server.config import (
    load_server_config, worker_status_map, tasks_lock, log_msg
)
import server.config as config_mod
from server.services.cluster_monitor import get_cached_cluster_data
from server.services.endpoint_service import normalize_endpoint
from server.services.task_service import (
    get_next_pending_task, load_tasks_unlocked, save_tasks_unlocked, update_task_status
)
from server.workers.task_runner import execute_task_on_server

# =========================================================================
# 工业级常驻弹性算力集群中枢 (Universal Elastic Cluster Dispatcher)
# 支持 1 ~ 100+ 节点全动态弹性扩展、常驻待命与 0ms 纯事件驱动
# =========================================================================

_cluster_wake_event = threading.Event()
_active_workers_lock = threading.Lock()
_active_workers = {}  # {sid: threading.Thread}
_supervisor_started = False
_supervisor_lock = threading.Lock()

cluster_paused = True
_selected_server_id = None
_dispatch_state_lock = threading.Lock()


def claim_next_task(srv_name, sid=None):
    """与暂停共用锁：暂停返回后不能再认领新任务。"""
    with _dispatch_state_lock:
        if cluster_paused or (_selected_server_id and sid != _selected_server_id):
            return None
        return get_next_pending_task(srv_name)

def notify_cluster_event(reason=""):
    """触发全局事件总线中断 (0ms 瞬时唤醒)"""
    _cluster_wake_event.set()

notify_dispatcher = notify_cluster_event


def worker_thread_loop(sid):
    """
    单个算力节点的消费线程 (针对 N 个节点独立隔离运行)
    """
    cfg = load_server_config()
    curr_srv = next((s for s in cfg.get("servers", []) if s.get("id") == sid), None)
    srv_name = curr_srv.get("name", sid) if curr_srv else sid
    log_msg(f"[Worker] 算力线程就绪: 绑定端口 [{srv_name}] (ID: {sid})")

    worker_status_map[sid] = {
        "name": srv_name,
        "state": "IDLE",
        "task_id": "",
        "updated_at": time.time()
    }

    consecutive_failures = 0

    try:
        while True:
            # 1. 实时检查端口启用状态
            cfg = load_server_config()
            curr_srv = next((s for s in cfg.get("servers", []) if s.get("id") == sid), None)
            if not curr_srv or not curr_srv.get("enabled", True):
                log_msg(f"[Worker] 端口 [{srv_name}] 已停用，该节点算力线程安全退出。")
                break
            srv_name = curr_srv.get("name", sid)

            # 如果用户手动点击了暂停，等待恢复
            if cluster_paused or (_selected_server_id and sid != _selected_server_id):
                worker_status_map[sid] = {
                    "name": srv_name,
                    "state": "IDLE",
                    "task_id": "",
                    "updated_at": time.time()
                }
                _cluster_wake_event.wait(timeout=1.5)
                continue

            # 2. 毫秒级异步健康感知 (直接读取内存缓存，耗时 < 0.1ms，杜绝 100 节点网络阻塞)
            cluster_data = get_cached_cluster_data()
            cached_servers = cluster_data.get("servers", [])
            cached_item = next((s for s in cached_servers if s.get("id") == sid), None)

            if cached_item is None or cached_item.get("url") != curr_srv.get("url"):
                # 新端口必须等第一次健康检查，不能先从生产队列领单。
                time.sleep(1)
                continue

            if cached_item:
                is_online = cached_item.get("status", {}).get("online", True)
                if not is_online:
                    worker_status_map[sid] = {
                        "name": srv_name,
                        "state": "OFFLINE",
                        "task_id": "",
                        "updated_at": time.time()
                    }
                    _cluster_wake_event.wait(timeout=2.0)
                    continue

            # 3. 熔断保护：连续失败 3 次进入 15 秒冷却
            if cached_item.get('status', {}).get('queue_running') or cached_item.get('status', {}).get('queue_pending'):
                worker_status_map[sid] = {'name': srv_name, 'state': 'WAITING', 'task_id': '', 'updated_at': time.time()}
                time.sleep(1)
                continue
            if consecutive_failures >= 3:
                log_msg(f"[Worker] 端口 [{srv_name}] 连续失败 {consecutive_failures} 次，进入 15 秒熔断保护...")
                worker_status_map[sid] = {
                    "name": srv_name,
                    "state": "COOLDOWN",
                    "task_id": "",
                    "updated_at": time.time()
                }
                time.sleep(15.0)
                consecutive_failures = 0
                continue

            # 4. 原子竞争领单 (Work-Stealing)
            task = claim_next_task(srv_name, sid)
            if not task:
                worker_status_map[sid] = {
                    "name": srv_name,
                    "state": "IDLE",
                    "task_id": "",
                    "updated_at": time.time()
                }
                # 队列暂无任务，待命等待事件唤醒（新任务下发、重做任务等瞬间唤醒，0 CPU 占用）
                _cluster_wake_event.wait(timeout=1.5)
                continue

            # 领单成功，进入 BUSY 状态开始渲染
            tid = task["task_id"]
            worker_status_map[sid] = {
                "name": srv_name,
                "state": "BUSY",
                "task_id": tid,
                "updated_at": time.time()
            }
            log_msg(f"[Worker] 端口 [{srv_name}] 成功认领任务 [{tid}]，开始并行渲染...")

            try:
                success = execute_task_on_server(curr_srv, task)
                if success:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except Exception as e:
                log_msg(f"[Worker] 端口 [{srv_name}] 执行任务 [{tid}] 发生未捕获异常: {e}")
                with tasks_lock:
                    current = next((t for t in load_tasks_unlocked() if t['task_id'] == tid), {})
                status = 'REVIEW' if current.get('status') in {'SUBMITTING','RUNNING','DOWNLOADING'} else 'FAILED'
                update_task_status(tid, status, worker_node=srv_name, error=str(e)[:1500])
                consecutive_failures += 1

            worker_status_map[sid] = {
                "name": srv_name,
                "state": "IDLE",
                "task_id": "",
                "updated_at": time.time()
            }
            # 完成后广播一次事件，唤醒其他协调逻辑
            notify_cluster_event()
            time.sleep(0.3)

    finally:
        with _active_workers_lock:
            if sid in _active_workers:
                del _active_workers[sid]
        cfg = load_server_config()
        curr_srv = next((s for s in cfg.get("servers", []) if s.get("id") == sid), None)
        is_en = curr_srv.get("enabled", True) if curr_srv else False
        worker_status_map[sid] = {
            "name": srv_name,
            "state": "IDLE" if is_en else "DISABLED",
            "task_id": "",
            "updated_at": time.time()
        }
        log_msg(f"[Worker] 算力线程安全退出: 端口 [{srv_name}]")


def sync_cluster_workers():
    """
    同步集群节点工作线程池（核心中枢）
    无论用户配置了 1 个、10 个还是 100 个节点：
    1. 自动为每一个处于 enabled=True 的节点拉起独立消费线程；
    2. 自动清理已终止的线程句柄；
    3. 停用节点时让其安全退出。
    """
    cfg = load_server_config()
    servers = cfg.get("servers", [])
    
    with _active_workers_lock:
        dead_sids = [sid for sid, th in list(_active_workers.items()) if not th.is_alive()]
        for sid in dead_sids:
            del _active_workers[sid]

        seen_urls = set()
        for srv in servers:
            sid = srv.get("id")
            is_enabled = srv.get("enabled", True)
            url = normalize_endpoint(srv.get('url', ''))
            if url in seen_urls:
                continue
            if is_enabled:
                seen_urls.add(url)
            srv_name = srv.get("name", sid)

            if is_enabled:
                if sid not in _active_workers or not _active_workers[sid].is_alive():
                    log_msg(f"[集群动态扩容] ⚡ 检测到算力端口 [{srv_name}] 处于启用状态，立即拉起专属算力线程投入生产！")
                    th = threading.Thread(
                        target=worker_thread_loop,
                        args=(sid,),
                        name=f"Worker-{srv_name}",
                        daemon=True
                    )
                    _active_workers[sid] = th
                    th.start()
            else:
                if sid not in _active_workers:
                    worker_status_map[sid] = {
                        "name": srv_name,
                        "state": "DISABLED",
                        "task_id": "",
                        "updated_at": time.time()
                    }


def _supervisor_daemon_loop():
    """
    常驻后台守护主循环 (永远不死，常驻待命，类似 Celery / Ray 主管进程)
    """
    global cluster_paused
    log_msg("=" * 65)
    log_msg("[*] 🚀 工业级常驻弹性集群中枢已就绪 (支持 1~100+ 节点全自动动态调度)！")
    log_msg("=" * 65)

    while True:
        try:
            _cluster_wake_event.clear()

            # 动态同步集群所有 N 个节点
            sync_cluster_workers()

            # 调度开关只由用户的启动/暂停操作改变，不能用排队数量自动开启。

            # 等待 1 秒或者任意事件（开启端口/添加任务/重新生成）瞬间唤醒
            _cluster_wake_event.wait(timeout=1.0)
        except Exception as e:
            log_msg(f"[!] 集群主管守护异常: {e}")
            time.sleep(2)


def start_dispatcher_daemon():
    """在系统启动时直接启动常驻集群中枢守护"""
    global _supervisor_started
    with _supervisor_lock:
        if not _supervisor_started:
            _supervisor_started = True
            t = threading.Thread(target=_supervisor_daemon_loop, name="ClusterSupervisorDaemon", daemon=True)
            t.start()


def run_multi_worker_batch(server_id=None):
    """启动/恢复集群生产"""
    global cluster_paused, _selected_server_id
    if server_id not in (None, '', 'all'):
        cfg = load_server_config()
        if not any(s['id'] == server_id and s.get('enabled', True) for s in cfg['servers']):
            raise ValueError('所选执行端口不存在或已停用')
    with _dispatch_state_lock:
        _selected_server_id = None if server_id in (None, '', 'all') else server_id
        cluster_paused = False
        config_mod.batch_is_active = True
    start_dispatcher_daemon()
    sync_cluster_workers()
    notify_cluster_event()
    log_msg("[*] 用户已激活集群生产，下发即时调度广播。")


def pause_cluster_batch():
    """暂停集群生产"""
    global cluster_paused
    with _dispatch_state_lock:
        cluster_paused = True
        config_mod.batch_is_active = False
    log_msg("[*] 收到用户停止指令，集群已暂停认领新任务。")
    notify_cluster_event()
