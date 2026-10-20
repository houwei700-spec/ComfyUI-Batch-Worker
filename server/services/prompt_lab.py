"""Independent durable experiments; one consumer per enabled server."""
import base64
import copy
import io
import json
import math
import os
from pathlib import Path
import random
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps
from server import config
from server.services.asset_service import get_dir_images
from server.services.path_service import resolve_under, valid_asset_name
from server.services.endpoint_service import normalize_endpoint, apply_model_overrides, validate_local_workflow, is_local_endpoint, find_submission_receipt
from server.services.task_service import load_tasks
from server.workers.task_runner import download_video, select_video_output

ROOT = Path(config.BASE_DIR) / 'prompt_lab'
WORKFLOWS = {'480p': 'workflow.json', '768p': 'workflow_768p.json',
             'single768p': 'workflow_single768p.json'}
ACTIVE = {'UPLOADING', 'SUBMITTING', 'RUNNING', 'DOWNLOADING'}
lock = threading.RLock()  # Also serializes the two UI start endpoints.
_enabled = False
_thread = None
_threads = {}
_workers = {}
_running_ids = set()
_starting = False
_generation = 0
_initialized = False
_notice = ''


class RemoteExecutionError(ValueError):
    """The server explicitly reports failure, rather than an unknown outcome."""


class MissingLocalReceipt(ValueError):
    """Repeated successful reads found no receipt; never resubmit this task."""


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
            tmp = f.name
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def read_store():
    path = ROOT / 'tasks.json'
    if not path.exists():
        return {'sequence': 0, 'tasks': [], 'requests': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data.get('tasks'), list):
        raise ValueError('测试任务文件损坏，请从备份检查，不能重建覆盖')
    return data


def save_store(data):
    atomic_json(ROOT / 'tasks.json', data)


def initialize():
    global _initialized
    with lock:
        if _initialized:
            return
        data = read_store()
        changed = False
        for task in data['tasks']:
            if task['status'] in ACTIVE:
                task.update(status='REVIEW', error='服务中断；请先检查回传，确认云端状态后再重做。')
                changed = True
        if changed:
            save_store(data)
        _initialized = True


def busy():
    return (_enabled or _starting or (_thread is not None and _thread.is_alive())
            or any(t.is_alive() for t in _threads.values()))


def state():
    initialize()
    with lock:
        data = read_store()
        tasks = [dict({k: v for k, v in t.items() if k != 'server_url'},
                      quality=t.get('quality', '480p')) for t in data['tasks']]
        servers = [{'id': s['id'], 'name': s.get('name', s['id'])}
                   for s in config.load_server_config().get('servers', []) if s.get('enabled', True)]
        workers = copy.deepcopy(_workers)
        for worker_state in workers.values():
            task = next((t for t in tasks if t['task_id'] == worker_state.get('task_id')), None)
            if task and task['status'] in ACTIVE:
                worker_state['state'] = task['status']
        return {'tasks': tasks, 'enabled': _enabled, 'busy': busy(), 'notice': _notice,
                'features': {'promote': True, 'single768p': True},
                'starting': _starting, 'workers': workers,
                'servers': servers, 'preferred_server_id': config.load_server_config().get('active_server_id'),
                'seconds': {'min': .2, 'max': 30, 'step': .1}}


def update(task_id, **fields):
    with lock:
        data = read_store()
        task = next(t for t in data['tasks'] if t['task_id'] == task_id)
        task.update(fields, updated_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        save_store(data)
        return copy.deepcopy(task)


LIBRARY_CATEGORIES = {'model': 'models', 'apparel': 'apparels', 'scene': 'scenes'}


def library_assets():
    return {category: get_dir_images(Path(config.ASSETS_DIR) / category)
            for category in LIBRARY_CATEGORIES.values()}


def asset_path(asset, role=None):
    if not isinstance(asset, dict):
        raise ValueError('请选择或上传参考图片')
    name = asset.get('file', '')
    if not isinstance(name, str) or not name or Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('图片路径无效')
    if asset.get('source') == 'library':
        category = asset.get('category')
        if not valid_asset_name(category, name) or (role and category != LIBRARY_CATEGORIES[role]):
            raise ValueError('素材库图片类别不匹配')
        resolved = resolve_under(config.ASSETS_DIR, f'{category}/{name}')
        path = Path(resolved) if resolved else None
        if path is None or path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}:
            raise ValueError('素材库图片路径无效')
    elif asset.get('source') in (None, 'upload'):
        path = ROOT / 'assets' / name
    else:
        raise ValueError('图片来源无效')
    if not path.is_file():
        raise ValueError('参考图片不存在，请重新选择或上传')
    return path


def upload_asset(body):
    try:
        raw = base64.b64decode(body.get('data', ''), validate=True)
    except Exception:
        raise ValueError('图片编码无效')
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError('每张图片需小于 20 MB')
    with Image.open(io.BytesIO(raw)) as img:
        if img.width * img.height > 40_000_000:
            raise ValueError('图片超过 4000 万像素，请缩小后上传')
        ext = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'BMP': '.bmp'}.get(img.format)
        if not ext:
            raise ValueError('请选择 JPG、PNG、WEBP 或 BMP 图片')
        img.verify()
    folder = ROOT / 'assets'
    folder.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex + ext
    with (folder / name).open('xb') as f:
        f.write(raw)
    return {'file': name, 'name': str(body.get('name', '图片'))[:200]}


def validate_row(row):
    prompt = row.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('每条任务必须填写提示词')
    if len(prompt) > 50000:
        raise ValueError('单条提示词不能超过 50000 字符')
    value = row.get('seconds')
    if isinstance(value, bool):
        raise ValueError('视频时长无效')
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        raise ValueError('请填写视频时长')
    if not math.isfinite(seconds) or not .2 <= seconds <= 30 or abs(seconds * 10 - round(seconds * 10)) > .00001:
        raise ValueError('视频时长为 0.2–30 秒，步长 0.1 秒')
    return {'prompt': prompt, 'seconds': seconds}


def enqueue(body):
    initialize()
    request_id = body.get('request_id', '')
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
        raise ValueError('缺少有效的提交编号')
    quality = body.get('quality', '480p')
    if quality not in WORKFLOWS:
        raise ValueError('请选择 480p 测试、一彩768p 或二彩928p')
    if not (ROOT / WORKFLOWS[quality]).is_file():
        raise ValueError('所选工作流文件不存在')
    with lock:
        data = read_store()
        if request_id in data['requests']:
            return {'created_ids': data['requests'][request_id]}
        rows = body.get('tasks')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ValueError('每次可加入 1–500 条任务，可分批追加')
        rows = [validate_row(row) for row in rows]
        assets = body.get('assets', {})
        for role in ('model', 'apparel'):
            asset_path(assets.get(role), role)
        if assets.get('scene'):
            asset_path(assets['scene'], 'scene')
        snapshot = {role: copy.deepcopy(assets.get(role)) for role in ('model', 'apparel', 'scene')}
        ids = []
        for row in rows:
            data['sequence'] += 1
            tid = f"LAB-{data['sequence']:06d}"
            ids.append(tid)
            data['tasks'].append(dict(row, task_id=tid, assets=snapshot, quality=quality, status='PENDING',
                                      output_file='', error='', prompt_id='', worker_node='',
                                      created_at=time.strftime('%Y-%m-%d %H:%M:%S')))
        data['requests'][request_id] = ids
        save_store(data)
        return {'created_ids': ids}


def promote(body):
    """Copy a completed test snapshot once, atomically; never launch a worker."""
    initialize()
    quality = body.get('quality', '768p')
    if quality not in ('single768p', '768p'):
        raise ValueError('请选择一彩768p或二彩928p成品')
    with lock:
        data = read_store()
        source = next((t for t in data['tasks'] if t['task_id'] == body.get('task_id')), None)
        if not source or source.get('quality', '480p') != '480p':
            raise ValueError('请选择一条 480p 测试任务')
        promoted = dict(source.get('promoted_tasks', {}))
        if source.get('promoted_task_id'):
            promoted.setdefault('768p', source['promoted_task_id'])
        if promoted.get(quality):
            return {'task_id': promoted[quality], 'already_added': True}
        if source['status'] != 'DONE' or not source.get('output_file'):
            raise ValueError('请等待测试视频生成完成后再加入成品队列')
        if not (ROOT / WORKFLOWS[quality]).is_file():
            raise ValueError('成品工作流文件不存在')
        row = validate_row(source)
        assets = copy.deepcopy(source.get('assets', {}))
        for role in ('model', 'apparel', 'scene'):
            if role != 'scene' or assets.get(role):
                asset_path(assets.get(role), role)
        data['sequence'] += 1
        tid = f"LAB-{data['sequence']:06d}"
        data['tasks'].append(dict(row, task_id=tid, quality=quality, assets=assets,
                                 status='PENDING', held_for_start=True, source_task_id=source['task_id'],
                                 output_file='', error='', prompt_id='', worker_node='',
                                 created_at=time.strftime('%Y-%m-%d %H:%M:%S')))
        promoted[quality] = tid
        source['promoted_tasks'] = promoted
        if quality == '768p':
            source['promoted_task_id'] = tid  # Keep old clients and historical links compatible.
        save_store(data)
        return {'task_id': tid, 'already_added': False}


def build_workflow(task, images):
    # Parse anew for each task; the supplied source JSON remains byte-for-byte unchanged.
    quality = task.get('quality', '480p')
    if quality not in WORKFLOWS:
        raise ValueError('未知视频清晰度')
    workflow_name = WORKFLOWS[quality]
    wf = json.loads((ROOT / workflow_name).read_text(encoding='utf-8'))
    if wf.get('7', {}).get('class_type') != 'MiniMaxH3Easy':
        raise ValueError('专用模板的提示词节点不匹配')
    final_node = '93' if quality == '768p' else '16'
    if wf.get(final_node, {}).get('class_type') != 'VHS_VideoCombine' or not wf[final_node]['inputs'].get('save_output'):
        raise ValueError('专用模板的最终视频输出节点不匹配')
    row = validate_row(task)
    inputs = wf['7']['inputs']
    inputs['prompt'] = row['prompt']
    inputs['seconds'] = row['seconds']
    wf['39']['inputs']['image'] = images['model']
    wf['291']['inputs']['image'] = images['apparel']
    if images.get('scene'):
        if quality == '768p':
            wf['292']['inputs']['image'] = images['scene']
        else:
            wf['292'] = {'class_type': 'LoadImage', 'inputs': {'image': images['scene']},
                         '_meta': {'title': '提示词测试：场景'}}
        inputs['media_3'] = ['292', 0]
        inputs['media_type_3'] = 'image'
    else:
        inputs.pop('media_3', None)
        inputs.pop('media_type_3', None)
        if quality == '768p':
            wf.pop('292', None)
    wf['14']['inputs']['noise_seed'] = random.randint(100000000000000, 999999999999999)
    if quality == '768p':
        wf['60']['inputs']['noise_seed'] = random.randint(100000000000000, 999999999999999)
    wf[final_node]['inputs']['filename_prefix'] = task['task_id'] + '_' + uuid.uuid4().hex[:8]
    return wf


def remote_json(url, payload=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8') if payload is not None else None,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def remote_image_data(path):
    raw = path.read_bytes()
    if len(raw) <= 20 * 1024 * 1024:
        return path.name, raw
    try:
        with Image.open(io.BytesIO(raw)) as source:
            image = ImageOps.exif_transpose(source)
            scale = min(1, 8192 / max(image.size), math.sqrt(32_000_000 / (image.width * image.height)))
            if scale < 1:
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
            quality = 94
            for attempt in range(8):
                result = io.BytesIO()
                image.save(result, format='WEBP', quality=quality)
                if result.tell() <= 19 * 1024 * 1024:
                    return path.stem + '-' + uuid.uuid4().hex[:8] + '.webp', result.getvalue()
                if attempt % 2 == 0:
                    quality = 82
                else:
                    image = image.resize((max(1, int(image.width * .85)), max(1, int(image.height * .85))), Image.Resampling.LANCZOS)
                    quality = 90
    except Exception as e:
        raise ValueError('素材库大图优化失败：' + str(e)[:200]) from e
    raise ValueError('素材库图片优化后仍超过 20 MB，请换一张图片')


def upload_remote(server, asset, role=None):
    path = asset_path(asset, role)
    filename, image_data = remote_image_data(path)
    filename = filename.replace('"', '_').replace('\r', '_').replace('\n', '_')
    boundary = 'Lab' + uuid.uuid4().hex
    fields = []
    for key, value in [('type', 'input'), ('subfolder', 'prompt_lab'), ('overwrite', 'false')]:
        fields.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    fields.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
    data = b''.join(fields) + image_data + f'\r\n--{boundary}--\r\n'.encode()
    req = urllib.request.Request(server + '/upload/image', data=data,
                                 headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                result = json.load(r)
            break
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    if not result.get('name') or result.get('type', 'input') != 'input':
        raise ValueError('云端图片上传响应无效')
    return '/'.join(filter(None, [result.get('subfolder', ''), result['name']]))


def preflight(server, tasks):
    if is_local_endpoint(server):
        srv = next((s for s in config.load_server_config()['servers'] if normalize_endpoint(s['url']) == normalize_endpoint(server)), {'url': server})
        for quality in {t.get('quality', '480p') for t in tasks}:
            path = ROOT / WORKFLOWS[quality]
            validate_local_workflow(srv, apply_model_overrides(srv, json.loads(path.read_text(encoding='utf-8'))))
    info = remote_json(server + '/object_info/MiniMaxH3Easy')['MiniMaxH3Easy']['input']
    fields = {k: v for group in info.values() if isinstance(group, dict) for k, v in group.items()}
    limits = fields['seconds'][1]
    for task in tasks:
        seconds = task['seconds']
        if not limits.get('min', .2) <= seconds <= limits.get('max', 30):
            raise ValueError(f"任务 {task['task_id']} 的时长不在当前节点支持范围内")
        if task['assets'].get('scene') and 'media_3' not in fields:
            raise ValueError('当前节点不支持第三张场景图')
    if any(task.get('quality') == '768p' for task in tasks):
        for cls in ('ResolutionSelector', 'ImageResizeKJv2', 'LTXVConcatAVLatent', 'easy cleanGpuUsed'):
            found = remote_json(server + '/object_info/' + urllib.parse.quote(cls, safe=''))
            if cls not in found:
                raise ValueError('当前端口缺少二彩928p工作流节点：' + cls)


def receive(task):
    tid = task['task_id']
    server, prompt_id = task['server_url'], task.get('prompt_id')
    if not prompt_id:
        prompt_id = find_submission_receipt(server, task.get('client_id'))
        if not prompt_id:
            raise ValueError('未找到这次提交的执行编号，请核对 ComfyUI 队列和历史后再决定是否重排。')
        update(tid, prompt_id=prompt_id)
    quality = task.get('quality', '480p')
    final_node = '93' if quality == '768p' else '16'
    deadline = time.monotonic() + 24 * 3600
    last_error = ''
    missing_since = None
    missing_checks = 0
    while time.monotonic() < deadline:
        try:
            history = remote_json(server + '/history/' + urllib.parse.quote(prompt_id, safe=''))
        except Exception as e:
            last_error = str(e)
            missing_since, missing_checks = None, 0
            time.sleep(4)
            continue
        job = history.get(prompt_id)
        if not job:
            if is_local_endpoint(server):
                try:
                    queue = remote_json(server + '/queue')
                    if not isinstance(queue.get('queue_running'), list) or not isinstance(queue.get('queue_pending'), list):
                        raise ValueError('本机队列响应不完整')
                    entries = queue['queue_running'] + queue['queue_pending']
                    if any(not isinstance(entry, list) or len(entry) < 4 for entry in entries):
                        raise ValueError('本机队列记录不完整')
                    queued = any(entry[1] == prompt_id or
                                 (task.get('client_id') and isinstance(entry[3], dict)
                                  and entry[3].get('client_id') == task['client_id'])
                                 for entry in entries)
                except Exception as e:
                    last_error = str(e)
                    missing_since, missing_checks = None, 0
                else:
                    if queued:
                        missing_since, missing_checks = None, 0
                    else:
                        now = time.monotonic()
                        if missing_since is None:
                            missing_since = now
                        missing_checks += 1
                        if missing_checks >= 3 and now - missing_since >= 30:
                            raise MissingLocalReceipt('本机队列和历史连续至少 30 秒均未找到此任务。原编号已保留，请核对 ComfyUI 输出后再决定是否重排；其他新任务可以启动。')
            time.sleep(4)
            continue
        missing_since, missing_checks = None, 0
        status = job.get('status', {})
        if status.get('status_str') == 'error':
            raise RemoteExecutionError('云端执行失败：' + json.dumps(status, ensure_ascii=False)[:3000])
        item = select_video_output(job.get('outputs', {}), final_node)
        if not item:
            raise ValueError(f'云端历史记录没有节点 {final_node} 的视频输出')
        update(tid, status='DOWNLOADING')
        ext = Path(item['filename']).suffix.lower()
        folder = {'768p': 'prompt_lab/768p', 'single768p': 'prompt_lab/single768p'}.get(quality, 'prompt_lab')
        rel = f"{folder}/{tid}/{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}{ext}"
        target = Path(config.OUTPUT_DIR) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        url = server + '/view?' + urllib.parse.urlencode({k: item.get(k, 'output' if k == 'type' else '') for k in ('filename', 'subfolder', 'type')})
        for attempt in range(3):
            try:
                download_video(url, str(target))
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)
        update(tid, status='DONE', output_file=rel, error='')
        return
    raise TimeoutError('等待回传超过 24 小时，可点击检查回传，勿重复提交。' + last_error[:300])


def execute(task, srv):
    tid = task['task_id']
    server = srv['url'].rstrip('/')
    task = update(tid, server_url=server, worker_node=srv.get('name', srv['id']), server_id=srv['id'])
    images = {role: upload_remote(server, asset, role) for role, asset in task['assets'].items() if asset}
    wf = apply_model_overrides(srv, build_workflow(task, images))
    atomic_json(ROOT / 'submissions' / (tid + '.json'), wf)
    client_id = tid + '_' + uuid.uuid4().hex
    update(tid, status='SUBMITTING', client_id=client_id)
    # A lost response is ambiguous: never automatically retry POST /prompt.
    result = remote_json(server + '/prompt', {'prompt': wf, 'client_id': client_id})
    if not result.get('prompt_id'):
        raise RuntimeError('提交响应缺少 prompt_id，请核对云端队列')
    task = update(tid, status='RUNNING', prompt_id=result['prompt_id'])
    receive(task)


def claim(srv, quality=None):
    """Pause and every server share this lock; each task can be claimed once."""
    with lock:
        if not _enabled:
            return None
        cfg = config.load_server_config()
        if not any(s['id'] == srv['id'] and s.get('enabled', True)
                   and s['url'].rstrip('/') == srv['url'].rstrip('/') for s in cfg.get('servers', [])):
            return None
        data = read_store()
        task = next((t for t in data['tasks'] if t['status'] == 'PENDING' and not t.get('held_for_start')
                     and (quality is None or t.get('quality', '480p') == quality)), None)
        if task:
            task = update(task['task_id'], status='UPLOADING', error='', server_id=srv['id'],
                          worker_node=srv.get('name', srv['id']))
            _workers[srv['id']] = {'name': srv.get('name', srv['id']), 'state': 'UPLOADING', 'task_id': task['task_id']}
        return task


def worker(srv, resume=None, quality=None):
    global _enabled, _notice
    sid = srv['id'] if srv else '_receive'
    try:
        while True:
            if srv and not resume and is_local_endpoint(srv['url']):
                with lock:
                    if not _enabled:
                        return
                    records = read_store()['tasks']
                    if not any(t['status'] in ACTIVE or (t['status'] == 'PENDING' and (quality is None or t.get('quality', '480p') == quality)) for t in records):
                        return
                    configured = config.load_server_config()['servers']
                    if not any(s['id'] == sid and s.get('enabled', True) and normalize_endpoint(s['url']) == normalize_endpoint(srv['url']) for s in configured):
                        return
                try:
                    queue = remote_json(srv['url'].rstrip('/') + '/queue')
                    waiting = bool(queue.get('queue_running') or queue.get('queue_pending'))
                    reason = '本机 ComfyUI 正在处理已有任务，完成后领取'
                except Exception as e:
                    waiting, reason = True, '等待本机 ComfyUI 恢复连接：' + str(e)[:300]
                if waiting:
                    with lock:
                        _workers[sid] = {'name': srv.get('name', sid), 'state': 'WAITING', 'task_id': '', 'error': reason}
                    time.sleep(2)
                    continue
            with lock:
                if resume:
                    task = resume
                else:
                    if not _enabled:
                        return
                    task = claim(srv, quality)
                    if not task:
                        # Keep idle servers available while other servers finish their work.
                        cfg = config.load_server_config()
                        available = any(s['id'] == sid and s.get('enabled', True)
                                        and s['url'].rstrip('/') == srv['url'].rstrip('/') for s in cfg.get('servers', []))
                        if not available:
                            _workers[sid] = {'name': srv.get('name', sid), 'state': 'DISABLED', 'task_id': ''}
                            return
                        if not any(t['status'] in ACTIVE for t in read_store()['tasks']):
                            _enabled = False
                            return
                if not task:
                    _workers[sid] = {'name': srv.get('name', sid), 'state': 'IDLE', 'task_id': ''}
            if not task:
                time.sleep(.25)
                continue
            try:
                if resume:
                    update(task['task_id'], status='RUNNING', error='', error_code='')
                    receive(task)
                else:
                    execute(task, srv)
            except Exception as e:
                with lock:
                    current = next(t for t in read_store()['tasks'] if t['task_id'] == task['task_id'])
                    ambiguous = current['status'] in {'SUBMITTING', 'RUNNING', 'DOWNLOADING'}
                    if isinstance(e, RemoteExecutionError):
                        ambiguous = False
                    if isinstance(e, urllib.error.HTTPError) and e.code == 400:
                        ambiguous = False
                        detail = e.read().decode('utf-8', errors='replace')[:3000]
                    else:
                        detail = str(e)[:3000]
                    update(task['task_id'], status='REVIEW' if ambiguous else 'FAILED', error=detail,
                           error_code='LOCAL_RECEIPT_MISSING' if isinstance(e, MissingLocalReceipt) else '')
                    _workers[sid] = {'name': srv.get('name', sid) if srv else '检查回传',
                                     'state': 'ERROR', 'task_id': task['task_id'], 'error': detail}
                    _notice = '有端口发生异常，该端口已停止领取；其他端口继续，详情见任务记录。'
                return
            if resume:
                return
            with lock:
                _workers[sid] = {'name': srv.get('name', sid), 'state': 'IDLE', 'task_id': ''}
    finally:
        with lock:
            if _workers.get(sid, {}).get('state') not in {'ERROR', 'DISABLED'}:
                _workers[sid] = {'name': srv.get('name', sid) if srv else '检查回传', 'state': 'STOPPED', 'task_id': ''}
            _running_ids.discard(sid)
            if not _running_ids:
                _enabled = False


def start(server_id=None, quality=None):
    global _enabled, _threads, _workers, _notice, _starting, _generation
    initialize()
    if quality is not None and quality not in WORKFLOWS:
        raise ValueError('未知视频清晰度')
    with lock:
        if busy():
            raise ValueError('测试任务仍在执行或回传，请等待当前任务结束')
        if config.batch_is_active or any(t['status'] in {'RUNNING', 'QUEUED', 'SUBMITTING', 'DOWNLOADING'} for t in load_tasks()):
            raise ValueError('请先暂停原生产队列，并等待活动任务完成')
        tasks = read_store()['tasks']
        if any(t['status'] == 'REVIEW' and t.get('error_code') != 'LOCAL_RECEIPT_MISSING'
               and (quality is None or t.get('quality', '480p') == quality) for t in tasks):
            raise ValueError('有待核对的任务，请先检查回传或确认重排')
        pending = [t for t in tasks if t['status'] == 'PENDING'
                   and (quality is None or t.get('quality', '480p') == quality)]
        if not pending:
            raise ValueError('请先加入当前清晰度的任务')
        servers = [copy.deepcopy(s) for s in config.load_server_config()['servers']
                   if s.get('enabled', True) and (server_id in (None, '', 'all') or s['id'] == server_id)]
        # Multiple names for one URL must never create duplicate consumers.
        servers = list({normalize_endpoint(s['url']): s for s in servers}.values())
        if not servers:
            raise ValueError('没有已启用的端口，请到模板生产的端口管理中启用')
        _starting = True
        _generation += 1
        generation = _generation
        _workers = {s['id']: {'name': s.get('name', s['id']), 'state': 'CHECKING', 'task_id': ''} for s in servers}
    def check(srv):
        server = srv['url'].rstrip('/')
        for attempt in range(3):
            try:
                preflight(server, pending)
                queue = remote_json(server + '/queue')
                if (queue.get('queue_running') or queue.get('queue_pending')) and not is_local_endpoint(server):
                    return srv, '云端已有其他任务，本次跳过此端口'
                return srv, ''
            except urllib.error.HTTPError as e:
                return srv, f'云端接口返回 HTTP {e.code}：{e.reason}'
            except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError) as e:
                if attempt == 2:
                    return srv, ('网络连接连续 3 次失败：' + str(e))[:1000]
                time.sleep(attempt + 1)
            except Exception as e:
                return srv, str(e)[:1000]
    try:
        with ThreadPoolExecutor(max_workers=min(16, len(servers))) as pool:
            checked = list(pool.map(check, servers))
        with lock:
            if generation != _generation or not _starting:
                raise ValueError('启动检查已被暂停取消，没有领取新任务')
            _starting = False
            ready = []
            for srv, error in checked:
                _workers[srv['id']] = {'name': srv.get('name', srv['id']),
                                       'state': 'UNAVAILABLE' if error else 'IDLE', 'task_id': '', 'error': error}
                if not error:
                    ready.append(srv)
            if not ready:
                if server_id in (None, '', 'all'):
                    _notice = '全部已启用端口本次均不可用，请查看各端口状态。'
                else:
                    _notice = f"所选端口 {servers[0].get('name', servers[0]['id'])} 不可用；可切换到“全部启用端口”重试。"
                raise ValueError(_notice)
            _notice = '部分端口不可用，已跳过；其余端口并行执行。' if len(ready) != len(servers) else ''
            # Only release promotions present when this explicit start began.
            ready_ids = {t['task_id'] for t in pending}
            data = read_store()
            changed = False
            for task in data['tasks']:
                if task['task_id'] in ready_ids and task['status'] == 'PENDING' and task.get('held_for_start'):
                    task['held_for_start'] = False
                    changed = True
            if changed:
                save_store(data)
            _enabled = True
            _threads = {s['id']: threading.Thread(target=worker, args=(s, None, quality), daemon=True,
                                                 name='PromptLab-' + s['id']) for s in ready}
            _running_ids.update(_threads)
            for thread in _threads.values():
                thread.start()
    finally:
        with lock:
            if generation == _generation:
                _starting = False


def pause():
    global _enabled, _starting, _generation
    with lock:
        _enabled = False
        _starting = False
        _generation += 1


def task_action(body):
    global _thread
    initialize()
    with lock:
        data = read_store()
        task = next((t for t in data['tasks'] if t['task_id'] == body.get('task_id')), None)
        if not task:
            raise ValueError('任务不存在')
        if task['status'] in ACTIVE:
            raise ValueError('活动任务不能修改')
        action = body.get('action')
        if action == 'receive':
            if busy() or not (task.get('prompt_id') or task.get('client_id')) or task['status'] not in {'REVIEW', 'FAILED'}:
                raise ValueError('当前无法检查回传；需要已有云端任务编号且测试队列空闲')
            _thread = threading.Thread(target=worker, args=(None, copy.deepcopy(task)), daemon=True, name='PromptLabReceive')
            _thread.start()
        elif action == 'retry':
            if busy() or task['status'] not in {'DONE', 'FAILED', 'REVIEW'}:
                raise ValueError('暂停并等待测试队列空闲后，才能重排终态任务')
            if task['status'] == 'REVIEW' and body.get('confirmed') is not True:
                raise ValueError('请先核对云端任务，确认重排可能再次生成')
            task.update(status='PENDING', prompt_id='', client_id='', error='', error_code='', worker_node='')
            save_store(data)
        elif action == 'delete':
            # Pause stops claims, but in-flight workers must keep receiving videos.
            # This lock also covers claim/update, so a pending task cannot be
            # claimed between this status check and the atomic deletion.
            if _enabled or _starting:
                raise ValueError('请先暂停队列领取，再删除任务')
            if task['status'] == 'REVIEW':
                raise ValueError('这条任务待核对，请先检查回传，不能直接删除')
            if task['status'] not in {'PENDING', 'DONE', 'FAILED'}:
                raise ValueError('当前任务状态不能删除')
            data['tasks'] = [t for t in data['tasks'] if t['task_id'] != task['task_id']]
            save_store(data)  # Retain sequence and videos.
        else:
            raise ValueError('未知任务操作')
