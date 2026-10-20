import os
import json
import time
import threading
import urllib.parse
import datetime
from server.config import (
    TEMPLATES_DIR, MODELS_DIR, APPARELS_DIR, SCENES_DIR, OUTPUT_DIR,
    load_server_config, save_server_config, get_active_server,
    tasks_lock, batch_stop_event, worker_status_map, log_msg
)
import server.config as config_mod
from server.services.cloud_service import probe_single_server
from server.services.cluster_monitor import get_cached_cluster_data, update_cluster_cache
from server.services.asset_service import get_dir_images
from server.services.task_service import load_tasks, load_tasks_unlocked, save_tasks_unlocked, reserve_task_ids_unlocked, update_task_status
from server.services.schema_service import inspect_template_schema
from server.services.path_service import resolve_under
from server.services.endpoint_service import normalize_endpoint
from server.workers.batch_dispatcher import run_multi_worker_batch, pause_cluster_batch, notify_cluster_event, sync_cluster_workers
from server.workers.task_runner import sanitize_path_component
from server.routes.static_handler import (
    serve_thumbnail_image, serve_asset_image, serve_video_stream, handle_open_output, handle_locate_file
)

def send_json(handler, data, status=200):
    encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(encoded)

def handle_get(handler, path, query_params):
    if path.startswith('/api/prompt_lab/'):
        from server.routes.prompt_lab_api import handle
        return handle(handler, path, 'GET')
    if path == "/api/bootstrap":
        templates = []
        if os.path.exists(TEMPLATES_DIR):
            for f in sorted(os.listdir(TEMPLATES_DIR)):
                if f.endswith(".json"):
                    schema = inspect_template_schema(os.path.join(TEMPLATES_DIR, f))
                    if schema:
                        templates.append(schema)

        data = {
            "templates": templates,
            "models": get_dir_images(MODELS_DIR),
            "apparels": get_dir_images(APPARELS_DIR),
            "scenes": get_dir_images(SCENES_DIR),
            "tasks": load_tasks()
        }
        send_json(handler, data)
        return True

    elif path == "/api/tasks":
        send_json(handler, load_tasks())
        return True

    elif path == "/api/batch_status":
        with tasks_lock:
            tasks = load_tasks_unlocked()
        pending = sum(1 for t in tasks if t.get("status") == "PENDING")
        running = sum(1 for t in tasks if t.get("status") in ["RUNNING", "QUEUED", "SUBMITTING", "DOWNLOADING"])
        done = sum(1 for t in tasks if t.get("status") == "DONE")
        failed = sum(1 for t in tasks if t.get("status") == "FAILED")

        res = {
            "is_running": config_mod.batch_is_active,
            "workers": worker_status_map,
            "pending": pending,
            "running": running,
            "done": done,
            "failed": failed,
            "total": len(tasks)
        }
        send_json(handler, res)
        return True

    elif path == "/api/servers":
        # 直接从内存缓存中获取，耗时 < 0.1ms，杜绝任何同步阻塞
        data = get_cached_cluster_data()
        send_json(handler, {
            "servers": data.get("servers", []),
            "active_server_id": data.get("active_server_id", "")
        })
        return True

    # 缩略图极速路由（15KB，专供 UI 预览）
    elif path.startswith("/api/asset/thumb/"):
        return serve_thumbnail_image(handler, path)

    # 资产原图路由
    elif path.startswith("/api/asset/"):
        return serve_asset_image(handler, path)

    elif path == "/api/video_stream":
        return serve_video_stream(handler, query_params)

    elif path == "/api/cloud_status":
        data = get_cached_cluster_data()
        servers = data.get("servers", [])
        active_id = data.get("active_server_id", "")
        active_srv = next((s for s in servers if s.get("id") == active_id), None)
        if active_srv:
            st = active_srv.get("status", {})
            res = {
                "online": st.get("online", False),
                "name": active_srv.get("name", "节点"),
                "url": active_srv.get("url", ""),
                "gpu_info": st.get("gpu_info", "")
            }
        else:
            res = {"online": False, "name": "无可用端口", "url": "", "gpu_info": ""}
        send_json(handler, res)
        return True

    elif path == "/api/output_folders":
        folders = []
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if os.path.exists(OUTPUT_DIR):
            for d in os.listdir(OUTPUT_DIR):
                full_d = os.path.join(OUTPUT_DIR, d)
                if os.path.isdir(full_d):
                    if d == today_str:
                        lbl = f"{d} (今天)"
                    elif d == yesterday_str:
                        lbl = f"{d} (昨天)"
                    else:
                        lbl = f"{d} (历史批次)"
                    folders.append({"folder": d, "label": lbl})
        folders.sort(key=lambda x: x["folder"], reverse=True)
        folders.append({"folder": "ALL", "label": "🌐 全部历史产出目录"})
        send_json(handler, {"folders": folders})
        return True

    elif path == "/api/apparel_matrix_status":
        folder = query_params.get("folder", [""])[0].strip()
        model = query_params.get("model", [""])[0].strip()
        template = query_params.get("template", [""])[0].strip()
        scene = query_params.get("scene", [""])[0].strip()
        clean_model = sanitize_path_component(os.path.splitext(os.path.basename(model))[0]) if model else ""

        if not folder or folder.upper() == "ALL":
            search_roots = [OUTPUT_DIR]
        else:
            safe_folder = resolve_under(OUTPUT_DIR, folder)
            if safe_folder and os.path.isdir(safe_folder):
                search_roots = [safe_folder]
            else:
                search_roots = [OUTPUT_DIR]

        found_videos = []
        for sroot in search_roots:
            for root, dirs, files in os.walk(sroot):
                for f in files:
                    if f.lower().endswith(".mp4"):
                        found_videos.append(os.path.join(root, f))

        with tasks_lock:
            active_tasks = load_tasks_unlocked()

        all_apparels = get_dir_images(APPARELS_DIR)
        items = []
        for app in all_apparels:
            sku = os.path.splitext(app)[0]
            matched_video = None
            matched_task_id = ""
            for vf in sorted(found_videos):
                rel_v = os.path.relpath(vf, OUTPUT_DIR).replace("\\", "/")
                parts = rel_v.split("/")
                recorded = next((t for t in active_tasks if t.get("output_file", "").replace("\\", "/") == rel_v), None)
                if recorded:
                    matches = (recorded.get("apparel") == app
                               and (not model or recorded.get("model") == model)
                               and (not template or (recorded.get("template") == template and recorded.get("scene", "") == scene)))
                else:
                    # 分层归档按完整目录名匹配，绝不回退到其他模特的同款成片。
                    matches = (len(parts) == 4 and parts[2] == sanitize_path_component(sku)
                               and (not clean_model or parts[1] == clean_model))
                    if matches and template:
                        name_parts = [sanitize_path_component(os.path.splitext(os.path.basename(x))[0])
                                      for x in [scene, template] if x]
                        matches = os.path.splitext(parts[-1])[0].partition("_")[2] == "_".join(name_parts)
                if matches:
                    matched_video = vf
                    matched_task_id = recorded.get("task_id", "") if recorded else ""
                    break

            if matched_video:
                rel_v = os.path.relpath(matched_video, OUTPUT_DIR).replace("\\", "/")
                mtime = os.path.getmtime(matched_video)
                time_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                items.append({
                    "filename": app,
                    "sku": sku,
                    "status": "DONE",
                    "output_video": rel_v,
                    "task_id": matched_task_id,
                    "created_time": time_str
                })
            else:
                is_queued = any(
                    t.get("apparel") == app and t.get("status") in ["PENDING", "RUNNING", "QUEUED", "DOWNLOADING"]
                    and (not model or t.get("model") == model)
                    and (not template or (t.get("template") == template and t.get("scene", "") == scene))
                    for t in active_tasks
                )
                items.append({
                    "filename": app,
                    "sku": sku,
                    "status": "QUEUED" if is_queued else "UNPRODUCED",
                    "output_video": "",
                    "created_time": ""
                })

        done_cnt = sum(1 for i in items if i["status"] == "DONE")
        unproduced_cnt = sum(1 for i in items if i["status"] == "UNPRODUCED")
        queued_cnt = sum(1 for i in items if i["status"] == "QUEUED")

        send_json(handler, {
            "folder": folder,
            "model": model,
            "items": items,
            "summary": {
                "total": len(items),
                "done": done_cnt,
                "unproduced": unproduced_cnt,
                "queued": queued_cnt
            }
        })
        return True

    return False

def handle_post(handler, path, query_params):
    if path.startswith('/api/prompt_lab/'):
        from server.routes.prompt_lab_api import handle
        return handle(handler, path, 'POST')
    if path == "/api/add_task":
        content_len = int(handler.headers.get("Content-Length", 0))
        body = json.loads(handler.rfile.read(content_len).decode("utf-8"))

        with tasks_lock:
            tasks = load_tasks_unlocked()
            next_id = reserve_task_ids_unlocked(tasks, 1)[0]
            new_task = {
                "task_id": next_id,
                "template": body.get("template", ""),
                "model": body.get("model", ""),
                "apparel": body.get("apparel", ""),
                "scene": body.get("scene", ""),
                "status": "PENDING",
                "output_file": "",
                "worker_node": ""
            }
            tasks.append(new_task)
            save_tasks_unlocked(tasks)

        notify_cluster_event()
        send_json(handler, {"success": True, "tasks": load_tasks()})
        return True

    elif path == "/api/batch_add_tasks":
        content_len = int(handler.headers.get("Content-Length", 0))
        body = json.loads(handler.rfile.read(content_len).decode("utf-8"))
        template = body.get("template", "")
        model = body.get("model", "")
        scene = body.get("scene", "")
        apparels = body.get("apparels", [])

        if not template or not model or not apparels:
            send_json(handler, {"success": False, "message": "模板、模特或服装列表不能为空"}, 400)
            return True

        created_ids = []
        with tasks_lock:
            tasks = load_tasks_unlocked()
            allocated_ids = reserve_task_ids_unlocked(tasks, len(apparels))

            for next_id, app_fn in zip(allocated_ids, apparels):
                created_ids.append(next_id)
                tasks.append({
                    "task_id": next_id,
                    "template": template,
                    "model": model,
                    "apparel": app_fn,
                    "scene": scene,
                    "status": "PENDING",
                    "output_file": "",
                    "worker_node": ""
                })
            save_tasks_unlocked(tasks)

        notify_cluster_event("batch_tasks_created")
        send_json(handler, {
            "success": True,
            "count": len(apparels),
            "created_ids": created_ids,
            "tasks": load_tasks()
        })
        return True

    elif path == "/api/clear_task_records":
        with tasks_lock:
            tasks = load_tasks_unlocked()
            remaining = [t for t in tasks if t.get("status", "").upper() not in ["DONE", "FAILED"]]
            cleared = len(tasks) - len(remaining)
            if cleared:
                # 先保存编号上限，再移除记录；不删除任何成品文件。
                reserve_task_ids_unlocked(tasks)
                save_tasks_unlocked(remaining)
        send_json(handler, {"success": True, "cleared": cleared, "tasks": load_tasks()})
        return True

    elif path.startswith("/api/delete_task"):
        target_id = query_params.get("id", [""])[0]
        with tasks_lock:
            tasks = load_tasks_unlocked()
            if any(t.get("task_id") == target_id and t.get("status", "").upper()
                   in ["RUNNING", "QUEUED", "SUBMITTING", "DOWNLOADING", "REVIEW"] for t in tasks):
                send_json(handler, {"success": False, "message": "任务正在执行，请完成后再删除"}, 409)
                return True
            reserve_task_ids_unlocked(tasks)
            tasks = [t for t in tasks if t.get("task_id") != target_id]
            save_tasks_unlocked(tasks)

        send_json(handler, {"success": True, "tasks": load_tasks()})
        return True

    elif path == "/api/task/receive":
        from server.services import prompt_lab
        from server.workers.task_runner import receive_task
        length = int(handler.headers.get('Content-Length', 0))
        body = json.loads(handler.rfile.read(length).decode('utf-8'))
        with prompt_lab.lock:
            if prompt_lab.busy() or config_mod.batch_is_active:
                send_json(handler, {'success': False, 'message': '请先暂停队列并等待活动任务完成'}, 409)
                return True
            with tasks_lock:
                records = load_tasks_unlocked()
                task = next((t for t in records if t['task_id'] == body.get('task_id')), None)
                if any(t['status'] in {'QUEUED','SUBMITTING','RUNNING','DOWNLOADING'} for t in records):
                    send_json(handler, {'success': False, 'message': '仍有活动任务，请稍后检查回传'}, 409)
                    return True
                if not task or task['status'] not in {'REVIEW','FAILED'} or not (task.get('prompt_id') or task.get('client_id')) or not task.get('server_url'):
                    send_json(handler, {'success': False, 'message': '该任务没有可检查的执行编号，请先核对端口队列'}, 400)
                    return True
                task.update(status='RUNNING', error='')
                save_tasks_unlocked(records)
                snapshot = dict(task)
            def receive_existing():
                try:
                    receive_task(snapshot)
                except Exception as e:
                    update_task_status(snapshot['task_id'], 'REVIEW', error=str(e)[:1500])
            threading.Thread(target=receive_existing, daemon=True, name='ReceiveExistingTask').start()
        send_json(handler, {'success': True})
        return True

    elif path == "/api/task/regenerate":
        content_len = int(handler.headers.get("Content-Length", 0))
        body = json.loads(handler.rfile.read(content_len).decode("utf-8"))
        tid = str(body.get("task_id", "")).strip()

        if not tid:
            send_json(handler, {"success": False, "message": "缺少 task_id"})
            return True

        target_task = None
        with tasks_lock:
            tasks = load_tasks_unlocked()
            for t in tasks:
                if t.get("task_id") == tid:
                    if t.get("status", "").upper() not in ["DONE", "FAILED", "REVIEW"]:
                        send_json(handler, {"success": False, "message": "任务已在排队或执行中，无需重复生成"}, 409)
                        return True
                    if t.get('status') == 'REVIEW' and body.get('confirmed') is not True:
                        send_json(handler, {'success': False, 'message': '请先核对执行端口；状态不确定时重排可能重复生成。'}, 409)
                        return True
                    t.update(prompt_id='', server_url='', final_node='', client_id='', error='')
                    t["status"] = "PENDING"
                    t["worker_node"] = ""
                    t["output_file"] = ""
                    target_task = dict(t)
                    break
            if target_task:
                save_tasks_unlocked(tasks)

        if not target_task:
            send_json(handler, {"success": False, "message": f"未找到任务 [{tid}]"})
            return True

        log_msg(f"[*] 任务 [{tid}] 已由用户触发【重新生成】，状态已重置为 PENDING。")

        notify_cluster_event("task_regenerated")

        send_json(handler, {"success": True, "task_id": tid, "tasks": load_tasks()})
        return True

    elif path == "/api/reset_failed":
        with tasks_lock:
            tasks = load_tasks_unlocked()
            for t in tasks:
                if t.get("status", "").upper() == "FAILED":
                    t["status"] = "PENDING"
                    t["worker_node"] = ""
            save_tasks_unlocked(tasks)

        notify_cluster_event("failed_tasks_reset")

        send_json(handler, {"success": True, "tasks": load_tasks()})
        return True

    elif path == "/api/server/add":
        content_len = int(handler.headers.get("Content-Length", 0))
        body = json.loads(handler.rfile.read(content_len).decode("utf-8"))

        name = body.get("name", "").strip() or "算力节点"
        try:
            url = normalize_endpoint(body.get('url'))
        except ValueError as e:
            send_json(handler, {'success': False, 'message': str(e)}, 400)
            return True

        cfg = load_server_config()
        servers = cfg.get("servers", [])
        
        existing = [s for s in servers if normalize_endpoint(s.get("url")) == url]
        if existing:
            send_json(handler, {"success": False, "message": "该端口地址已存在！"})
            return True

        new_id = f"srv_{int(time.time()*1000)}"
        new_server = {
            "id": new_id,
            "name": name,
            "url": url,
            "enabled": True
        }
        servers.append(new_server)
        cfg["servers"] = servers
        save_server_config(cfg)
        log_msg(f"[+] 新增算力端口: {name} ({url})")
        threading.Thread(target=update_cluster_cache, daemon=True).start()
        sync_cluster_workers()
        notify_cluster_event()

        send_json(handler, {"success": True, "server": new_server})
        return True

    elif path == "/api/server/toggle":
        content_len = int(handler.headers.get("Content-Length", 0))
        body = json.loads(handler.rfile.read(content_len).decode("utf-8"))
        sid = body.get("id")
        enabled = bool(body.get("enabled", True))

        cfg = load_server_config()
        for s in cfg.get("servers", []):
            if s["id"] == sid:
                s["enabled"] = enabled
                log_msg(f"[*] 端口 [{s.get('name')}] 状态切换为: {'启用' if enabled else '停用'}")
        
        if not enabled and cfg.get("active_server_id") == sid:
            available = [s["id"] for s in cfg.get("servers", []) if s.get("enabled", True) and s["id"] != sid]
            if available:
                cfg["active_server_id"] = available[0]

        save_server_config(cfg)
        threading.Thread(target=update_cluster_cache, daemon=True).start()

        # 核心：无论 2 个还是 100 个节点，任何时候启用端口立即同步集群并唤醒调度
        sync_cluster_workers()
        notify_cluster_event()

        send_json(handler, {"success": True})
        return True

    elif path == "/api/server/set_active":
        content_len = int(handler.headers.get("Content-Length", 0))
        body = json.loads(handler.rfile.read(content_len).decode("utf-8"))
        sid = body.get("id")

        cfg = load_server_config()
        target_srv = None
        for s in cfg.get("servers", []):
            if s["id"] == sid:
                s["enabled"] = True
                target_srv = s
                break

        if target_srv:
            cfg["active_server_id"] = sid
            save_server_config(cfg)
            log_msg(f"[*] 当前默认端口切换为: {target_srv.get('name')} ({target_srv.get('url')})")
            threading.Thread(target=update_cluster_cache, daemon=True).start()

        send_json(handler, {"success": True})
        return True

    elif path == "/api/server/delete":
        content_len = int(handler.headers.get("Content-Length", 0))
        body = json.loads(handler.rfile.read(content_len).decode("utf-8"))
        sid = body.get("id")

        cfg = load_server_config()
        servers = cfg.get("servers", [])
        if len(servers) <= 1:
            send_json(handler, {"success": False, "message": "至少需要保留一个算力端口！"})
            return True

        cfg["servers"] = [s for s in servers if s["id"] != sid]
        if cfg.get("active_server_id") == sid:
            enabled_srvs = [s["id"] for s in cfg["servers"] if s.get("enabled", True)]
            cfg["active_server_id"] = enabled_srvs[0] if enabled_srvs else cfg["servers"][0]["id"]

        save_server_config(cfg)
        log_msg(f"[*] 删除端口: ID={sid}")
        threading.Thread(target=update_cluster_cache, daemon=True).start()
        send_json(handler, {"success": True})
        return True

    elif path == "/api/open_output":
        handle_open_output(handler)
        return True

    elif path.startswith("/api/locate_file"):
        handle_locate_file(handler, query_params)
        return True

    elif path == "/api/start_batch":
        from server.services import prompt_lab
        with prompt_lab.lock:
            if prompt_lab.busy():
                send_json(handler, {"success": False, "message": "提示词测试仍在执行或回传，请先暂停并等待完成"}, 409)
                return True
            length = int(handler.headers.get('Content-Length', 0))
            body = json.loads(handler.rfile.read(length).decode('utf-8')) if length else {}
            try:
                run_multi_worker_batch(body.get('server_id'))
            except ValueError as e:
                send_json(handler, {'success': False, 'message': str(e)}, 400)
                return True
        send_json(handler, {"success": True})
        return True

    elif path == "/api/stop_batch":
        pause_cluster_batch()
        send_json(handler, {"success": True})
        return True

    return False
