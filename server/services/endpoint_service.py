import ipaddress
import json
import urllib.request
import urllib.parse
from urllib.parse import urlsplit, urlunsplit


def normalize_endpoint(value):
    value = str(value or '').strip()
    if not value:
        raise ValueError('请输入 ComfyUI 地址')
    if '://' not in value:
        value = 'http://' + value
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请填写 http:// 或 https:// 开头的 ComfyUI 根地址')
    host = parsed.hostname.lower()
    if host == 'localhost':
        host = '127.0.0.1'
    port = parsed.port
    if host in ('127.0.0.1', '::1') and port == 8999:
        raise ValueError('8999 是生产工作台端口；ComfyUI 通常使用 8188')
    netloc = '[' + host + ']' if ':' in host else host
    if port:
        netloc += ':' + str(port)
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip('/'), '', ''))


def is_local_endpoint(url):
    host = urlsplit(url).hostname
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def apply_model_overrides(server, workflow):
    for node in workflow.values():
        overrides = server.get('model_overrides', {}).get(node.get('class_type'), {})
        for key, value in overrides.items():
            if key in node.get('inputs', {}):
                node['inputs'][key] = value
    return workflow


def validate_local_workflow(server, workflow):
    if not is_local_endpoint(server['url']):
        return
    for cls in sorted({node['class_type'] for node in workflow.values()}):
        url = server['url'].rstrip('/') + '/object_info/' + urllib.parse.quote(cls, safe='')
        with urllib.request.urlopen(url, timeout=10) as response:
            info = json.load(response)
        if cls not in info:
            raise ValueError('本机 ComfyUI 缺少工作流节点：' + cls)
        fields = {k: v for group in info[cls].get('input', {}).values() if isinstance(group, dict) for k, v in group.items()}
        for node in workflow.values():
            if node['class_type'] != cls:
                continue
            for key, value in node.get('inputs', {}).items():
                spec = fields.get(key, [])
                if spec and isinstance(spec[0], list) and isinstance(value, str) and key != 'image' and value not in spec[0]:
                    raise ValueError(f'本机不支持工作流参数 {cls}.{key}：{value}；请检查模型和节点版本')


def find_submission_receipt(server, client_id):
    """Read-only recovery after a lost POST response; match a unique attempt token."""
    if not client_id:
        return None
    def read(path):
        with urllib.request.urlopen(server.rstrip('/') + path, timeout=20) as response:
            return json.load(response)
    queue = read('/queue')
    entries = list(queue.get('queue_running', [])) + list(queue.get('queue_pending', []))
    queued = {entry[1] for entry in entries if isinstance(entry, list) and len(entry) > 3
              and isinstance(entry[3], dict) and entry[3].get('client_id') == client_id}
    if queued:
        return next(iter(queued)) if len(queued) == 1 else None
    history = read('/history?max_items=1000')
    entries.extend(job.get('prompt', []) for job in history.values() if isinstance(job, dict))
    matches = {entry[1] for entry in entries if isinstance(entry, list) and len(entry) > 3
               and isinstance(entry[3], dict) and entry[3].get('client_id') == client_id}
    return next(iter(matches)) if len(matches) == 1 else None
