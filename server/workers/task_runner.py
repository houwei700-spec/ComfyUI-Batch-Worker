import os
import json
import time
import random
import tempfile
import urllib.request
import urllib.parse
import urllib.error
import uuid
from server.config import TEMPLATES_DIR, OUTPUT_DIR, log_msg
from server.services.asset_service import resolve_cloud_image_path
from server.services.task_service import update_task_status
from server.services.path_service import resolve_under
from server.services.endpoint_service import is_local_endpoint, apply_model_overrides, validate_local_workflow, find_submission_receipt


def download_video(url, local_path, timeout=30, max_seconds=300):
    """下载成功后才替换成品，网络故障不破坏已有文件。"""
    tmp_path = None
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            expected = response.headers.get("Content-Length")
            with tempfile.NamedTemporaryFile(dir=os.path.dirname(local_path), suffix=".part", delete=False) as f:
                tmp_path = f.name
                size = 0
                while True:
                    if time.monotonic() - started > max_seconds:
                        raise TimeoutError("成品下载超过时间限制")
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    f.write(block)
                    size += len(block)
                if size <= 1024 or (expected is not None and size != int(expected)):
                    raise IOError("成品下载不完整")
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp_path, local_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def select_video_output(outputs, final_node):
    """只回传选定合成节点的最终视频，避免预览/一采覆盖二采。"""
    output = outputs.get(str(final_node), {})
    candidates = [item for key in ("gifs", "videos") for item in output.get(key, [])
                  if os.path.splitext(item.get("filename", ""))[1].lower() in {".mp4", ".webm", ".mov", ".mkv"}]
    if not candidates:
        return None
    return next((item for item in reversed(candidates) if "-audio" in item.get("filename", "")), candidates[-1])


def sanitize_path_component(name: str) -> str:
    """清理文件名/目录名中的非法字符，避免在 Windows 磁盘创建失败"""
    if not name:
        return ""
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip(". ")

def build_output_target(t, ext=".mp4"):
    """
    根据【形态 2】：
    output/
    └── 日期 (2026-09-08)/
        └── 模特分类 (仟慕女装_01)/
            └── 服装款号 (SJYF5316)/
                └── 任务编号_场景_镜头模板.mp4
    """
    date_str = time.strftime("%Y-%m-%d")
    
    # 模特分类
    raw_model = t.get("model", "")
    model_name = sanitize_path_component(os.path.splitext(os.path.basename(raw_model))[0]) if raw_model else "默认模特"
    
    # 服装款号
    raw_apparel = t.get("apparel", "")
    apparel_name = sanitize_path_component(os.path.splitext(os.path.basename(raw_apparel))[0]) if raw_apparel else "默认服装"
    
    # 场景名
    raw_scene = t.get("scene", "")
    scene_name = sanitize_path_component(os.path.splitext(os.path.basename(raw_scene))[0]) if raw_scene else ""
    
    # 镜头模板名
    raw_wf = t.get("template", "")
    wf_name = sanitize_path_component(os.path.splitext(os.path.basename(raw_wf))[0]) if raw_wf else ""
    
    # 任务编号
    tid_str = sanitize_path_component(str(t.get("task_id", "000"))).zfill(3)
    
    # 组装视频文件名称: 编号_场景_模板
    name_parts = [tid_str]
    if scene_name:
        name_parts.append(scene_name)
    if wf_name:
        name_parts.append(wf_name)
    if len(name_parts) == 1:
        name_parts.append(apparel_name)
        
    local_filename = "_".join(name_parts) + ext
    
    # 相对路径 (统一使用正斜杠供 CSV 存储、前端交互与 URL 使用)
    rel_path = f"{date_str}/{model_name}/{apparel_name}/{local_filename}"
    
    # 本地完整绝对路径 (自动递归创建子文件夹)
    target_dir = os.path.join(OUTPUT_DIR, date_str, model_name, apparel_name)
    os.makedirs(target_dir, exist_ok=True)
    local_path = os.path.join(target_dir, local_filename)
    
    return local_path, rel_path

def execute_task_on_server(srv, t):
    """在指定算力节点执行单项任务并下载回传本地"""
    tid = t["task_id"]
    server = srv.get("url", "").rstrip("/")
    srv_name = srv.get("name", srv.get("id", "算力节点"))

    tpl_file = resolve_under(TEMPLATES_DIR, t["template"])
    if not tpl_file or not os.path.isfile(tpl_file):
        log_msg(f"[-] 任务 [{tid}] 模板文件不存在: {t['template']}")
        update_task_status(tid, "FAILED", worker_node=srv_name)
        return False

    try:
        with open(tpl_file, "r", encoding="utf-8") as f:
            wf = json.load(f)
        apply_model_overrides(srv, wf)
        validate_local_workflow(srv, wf)
    except Exception as e:
        log_msg(f"[-] 任务 [{tid}] 模板解析失败: {e}")
        update_task_status(tid, "FAILED", worker_node=srv_name, error=str(e)[:1500])
        return False

    # 1. 动态自适应解析各槽位 Node ID
    model_nid = "18"
    apparel_nid = "35"
    scene_nid = "36"
    vhs_nid = "16"
    seed_nid = "14"

    for nid, node in wf.items():
        ctype = node.get("class_type", "")
        if ctype == "MiniMaxH3Easy":
            inp = node.get("inputs", {})
            if "media_1" in inp and inp["media_1"]:
                model_nid = str(inp["media_1"][0])
            if "media_2" in inp and inp["media_2"]:
                apparel_nid = str(inp["media_2"][0])
            if "media_3" in inp and inp["media_3"]:
                scene_nid = str(inp["media_3"][0])
        elif ctype == "VHS_VideoCombine" and node.get("inputs", {}).get("save_output", True):
            vhs_nid = nid
        elif ctype in ["RandomNoise", "KSampler"]:
            seed_nid = nid

    # 2. 毫秒级直连云端路径 (0秒本地上传)
    try:
        if is_local_endpoint(server):
            from server.services.prompt_lab import upload_remote
            def image_path(category, role, filename):
                return upload_remote(server, {'source': 'library', 'category': category,
                                              'file': filename, 'name': filename}, role) if filename else ''
            m_cloud_path = image_path('models', 'model', t['model'])
            a_cloud_path = image_path('apparels', 'apparel', t['apparel'])
            s_cloud_path = image_path('scenes', 'scene', t.get('scene', ''))
        else:
            m_cloud_path = resolve_cloud_image_path(server, "models", t["model"])
            a_cloud_path = resolve_cloud_image_path(server, "apparels", t["apparel"])
            s_cloud_path = resolve_cloud_image_path(server, "scenes", t.get("scene", "")) if t.get("scene") else ""
    except Exception as e:
        update_task_status(tid, 'FAILED', worker_node=srv_name, error='素材准备失败：' + str(e)[:1500])
        return False

    log_msg(f"[*] 任务 [{tid}] 素材直连挂载 ({srv_name}): 模特={m_cloud_path} | 服装={a_cloud_path} | 场景={s_cloud_path}")

    if model_nid in wf:
        wf[model_nid]["inputs"]["image"] = m_cloud_path
    if apparel_nid in wf:
        wf[apparel_nid]["inputs"]["image"] = a_cloud_path
    if scene_nid in wf and s_cloud_path:
        wf[scene_nid]["inputs"]["image"] = s_cloud_path

    if seed_nid in wf:
        inp = wf[seed_nid].get("inputs", {})
        if "noise_seed" in inp:
            inp["noise_seed"] = random.randint(100000000000000, 999999999999999)
        elif "seed" in inp:
            inp["seed"] = random.randint(100000000000000, 999999999999999)

    clean_apparel_name = os.path.splitext(t["apparel"])[0]
    if vhs_nid in wf:
        clean_name = f"Auto_{tid}_{clean_apparel_name}"
        wf[vhs_nid]["inputs"]["filename_prefix"] = clean_name

    # 3. 提交 Prompt
    client_id = 'batch_' + tid + '_' + uuid.uuid4().hex
    update_task_status(tid, "SUBMITTING", worker_node=srv_name, prompt_id='',
                       server_url=server, final_node=str(vhs_nid), client_id=client_id, error='')

    payload = {"prompt": wf, "client_id": client_id}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(f"{server}/prompt", data=data, headers={"Content-Type": "application/json"})

    prompt_id = None
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            p_data = json.loads(resp.read().decode("utf-8"))
            prompt_id = p_data.get("prompt_id")
            if not prompt_id:
                raise ValueError("云端响应缺少 prompt_id")
            log_msg(f"[+] 任务 [{tid}] 成功推入节点 [{srv_name}]！Prompt ID: {prompt_id}")
    except urllib.error.HTTPError as he:
        err_b = he.read().decode("utf-8", errors="ignore")
        log_msg(f"[-] 任务 [{tid}] 参数校验拦截 (HTTP {he.code}) [{srv_name}]: {err_b}")
        update_task_status(tid, "FAILED" if he.code == 400 else "REVIEW", worker_node=srv_name, error=err_b[:3000])
        return False
    except Exception as ex:
        log_msg(f"[-] 任务 [{tid}] 推送失败 [{srv_name}]: {ex}")
        update_task_status(tid, "REVIEW", worker_node=srv_name, error='提交结果不确定，请核对端口队列；不要直接重做。' + str(ex)[:1500])
        return False

    # Persist the receipt before polling, so a restart can receive the same job.
    update_task_status(tid, "RUNNING", worker_node=srv_name, prompt_id=prompt_id)
    return receive_task(dict(t, server_url=server, prompt_id=prompt_id, final_node=str(vhs_nid), worker_node=srv_name))


def receive_task(t, max_wait_seconds=24 * 3600):
    """Only inspect and download an existing job; this path never submits generation."""
    tid, server, prompt_id = t['task_id'], t['server_url'], t.get('prompt_id')
    if not prompt_id:
        try:
            prompt_id = find_submission_receipt(server, t.get('client_id'))
        except Exception as e:
            update_task_status(tid, 'REVIEW', error='暂时无法核对执行编号：' + str(e)[:1500])
            return False
        if not prompt_id:
            update_task_status(tid, 'REVIEW', error='未找到这次提交的执行编号，请核对 ComfyUI 队列与历史后决定是否重排。')
            return False
        update_task_status(tid, 'RUNNING', prompt_id=prompt_id)
    final_node = t.get('final_node') or '16'
    started = time.monotonic()
    last_error = ''
    while time.monotonic() - started < max_wait_seconds:
        try:
            url = server + '/history/' + urllib.parse.quote(prompt_id, safe='')
            with urllib.request.urlopen(url, timeout=15) as response:
                job = json.load(response).get(prompt_id)
            if not job:
                time.sleep(4)
                continue
            if job.get('status', {}).get('status_str') == 'error':
                update_task_status(tid, 'FAILED', error=json.dumps(job.get('status'), ensure_ascii=False)[:3000])
                return False
            item = select_video_output(job.get('outputs', {}), final_node)
            if not item:
                update_task_status(tid, 'REVIEW', error='执行端口已返回记录，但没有最终视频；请核对输出后再重做。')
                return False
            update_task_status(tid, 'DOWNLOADING')
            url = server + '/view?' + urllib.parse.urlencode({
                'filename': item['filename'], 'subfolder': item.get('subfolder', ''), 'type': item.get('type', 'output')})
            local_path, rel_path = build_output_target(t, os.path.splitext(item['filename'])[1])
            for attempt in range(3):
                try:
                    download_video(url, local_path)
                    update_task_status(tid, 'DONE', output_file=rel_path, error='')
                    return True
                except Exception as error:
                    last_error = str(error)
                    time.sleep(2)
            update_task_status(tid, 'REVIEW', error='视频下载未完成，可检查回传，无需重新生成：' + last_error[:1500])
            return False
        except Exception as error:
            last_error = str(error)
            time.sleep(4)
    update_task_status(tid, 'REVIEW', error='等待回传超时，保留任务编号；可检查回传。' + last_error[:1500])
    return False
