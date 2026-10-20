"""Isolated UI acceptance server. All cloud traffic terminates on localhost."""
import io
import json
from pathlib import Path
import sys
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.main import ModularHubHandler
from server.services import prompt_lab as lab
from server import config
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'lab_acceptance'
ROOT.mkdir(exist_ok=True)
(ROOT / 'workflow.json').write_bytes((lab.ROOT / 'workflow.json').read_bytes())
lab.ROOT = ROOT
config.OUTPUT_DIR = str(ROOT / 'output')
# Static video serving imports its output root by value.
from server.routes import static_handler
static_handler.OUTPUT_DIR = config.OUTPUT_DIR
config.load_server_config = lambda: {'servers': [{'id': 'mock-'+str(i), 'name': '离线模拟端口 '+str(i+1), 'url': 'http://127.0.0.1:'+str(19100+i), 'enabled': True} for i in range(3)]}
lab.load_tasks = lambda: []
for name, color in [('model', '#e7c8bb'), ('apparel', '#353640'), ('scene', '#c2d6c4')]:
    Image.new('RGB', (160, 240), color).save(ROOT / (name + '.png'))
video = b'mock-preview-video'
submissions = []
uploads = []
mock_lock = threading.Lock()
history_polls = {}


class Cloud(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def send(self, data, content_type='application/json'):
        if not isinstance(data, bytes): data = json.dumps(data).encode()
        self.send_response(200); self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        if self.path == '/upload/image':
            msg = BytesParser(policy=default).parsebytes(('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + raw)
            part = next(p for p in msg.iter_parts() if p.get_param('name', header='content-disposition') == 'image')
            Image.open(io.BytesIO(part.get_payload(decode=True))).verify()
            uploads.append(part.get_filename())
            self.send({'name': part.get_filename(), 'subfolder': 'prompt_lab', 'type': 'input'})
        elif self.path == '/prompt':
            data = json.loads(raw)
            with mock_lock:
                data['mock_port'] = self.server.server_port
                submissions.append(data)
                pid = 'mock-' + str(len(submissions))
                history_polls[pid] = 0
                (ROOT / 'captured-submissions.json').write_text(json.dumps({'uploads': uploads, 'submissions': submissions}, ensure_ascii=False, indent=2), encoding='utf-8')
            self.send({'prompt_id': pid})
    def do_GET(self):
        if self.path.startswith('/object_info/'):
            self.send({'MiniMaxH3Easy': {'input': {'required': {'seconds': ['FLOAT', {'min': .2, 'max': 30, 'step': .1}]}, 'optional': {'media_3': ['*']}}}})
        elif self.path.startswith('/history/'):
            pid = self.path.rsplit('/', 1)[-1]
            with mock_lock:
                history_polls[pid] += 1
                waiting = history_polls[pid] <= 2
            if waiting:
                self.send({}); return
            self.send({pid: {'status': {'status_str': 'success'}, 'outputs': {'16': {'gifs': [{'filename': 'offline.mp4', 'subfolder': '', 'type': 'output'}]}}}})
        elif self.path == '/queue': self.send({'queue_running': [], 'queue_pending': []})
        elif self.path.startswith('/view?'): self.send(video, 'video/mp4')


for port in range(19100,19103):
    cloud = ThreadingHTTPServer(('127.0.0.1', port), Cloud)
    threading.Thread(target=cloud.serve_forever, daemon=True).start()
print('Offline acceptance: http://127.0.0.1:19099/prompt-lab.html', flush=True)
ThreadingHTTPServer(('127.0.0.1', 19099), ModularHubHandler).serve_forever()
