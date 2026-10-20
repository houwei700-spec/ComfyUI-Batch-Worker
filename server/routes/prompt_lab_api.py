import json
import urllib.parse
from server.services import prompt_lab as lab
from server.routes.static_handler import serve_static_file


def handle(handler, path, method):
    from server.routes.api_router import send_json
    try:
        if method == 'GET' and path == '/api/prompt_lab/state':
            send_json(handler, dict(lab.state(), success=True))
        elif method == 'GET' and path == '/api/prompt_lab/library':
            send_json(handler, dict(lab.library_assets(), success=True))
        elif method == 'GET' and path.startswith('/api/prompt_lab/asset/'):
            name = urllib.parse.unquote(path.rsplit('/', 1)[-1])
            serve_static_file(handler, str(lab.asset_path({'file': name})))
        elif method == 'POST':
            length = int(handler.headers.get('Content-Length', 0))
            if not 0 < length <= 29 * 1024 * 1024:
                send_json(handler, {'success': False, 'message': '请求体为空或超过 29 MB'}, 413)
                return True
            body = json.loads(handler.rfile.read(length).decode('utf-8'))
            if not isinstance(body, dict):
                raise ValueError('请求必须为 JSON 对象')
            result = {}
            if path == '/api/prompt_lab/upload':
                result['asset'] = lab.upload_asset(body)
            elif path == '/api/prompt_lab/enqueue':
                result = lab.enqueue(body)
            elif path == '/api/prompt_lab/promote':
                result = lab.promote(body)
            elif path == '/api/prompt_lab/start':
                lab.start(body.get('server_id'), body.get('quality'))
            elif path == '/api/prompt_lab/pause':
                lab.pause()
            elif path == '/api/prompt_lab/task':
                lab.task_action(body)
            else:
                send_json(handler, {'success': False, 'message': '接口不存在'}, 404)
                return True
            send_json(handler, dict(result, success=True))
        else:
            send_json(handler, {'success': False, 'message': '接口不存在'}, 404)
    except (ValueError, KeyError, TypeError) as e:
        send_json(handler, {'success': False, 'message': str(e)}, 400)
    except Exception as e:
        send_json(handler, {'success': False, 'message': '操作失败：' + str(e)[:1500]}, 500)
    return True
