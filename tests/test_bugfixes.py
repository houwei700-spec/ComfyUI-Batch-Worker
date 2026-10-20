"""离线回归测试：所有写入仅发生在临时目录，不连接 ComfyUI。"""
import csv
import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from server.services import task_service as tasks
from server.services import thumbnail_service as thumbs
from server.services.path_service import resolve_under
from server.routes import api_router as api
from server.routes import static_handler as static
from server.workers import task_runner as runner


class Handler:
    def __init__(self, body=None, headers=None):
        data = json.dumps(body or {}).encode()
        self.headers = {"Content-Length": str(len(data)), **(headers or {})}
        self.rfile, self.wfile = io.BytesIO(data), io.BytesIO()
        self.status, self.response_headers = None, {}

    def send_response(self, code): self.status = code
    def send_header(self, name, value): self.response_headers[name] = value
    def end_headers(self): pass
    def result(self): return json.loads(self.wfile.getvalue())


class BugfixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task_path = str(self.root / 'tasks.csv')
        for target, value in [("server.services.task_service.TASK_FILE", self.task_path),
                              ("server.routes.api_router.load_server_config", lambda: {"servers": []}),
                              ("server.routes.api_router.log_msg", lambda *a: None),
                              ("server.workers.task_runner.log_msg", lambda *a: None)]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)

    def task(self, tid='001', status='PENDING', **extra):
        return dict(task_id=tid, status=status, template='tpl.json', model='model.jpg',
                    apparel='sku.jpg', scene='', output_file='', worker_node='', **extra)

    def test_reset_does_not_touch_active_tasks(self):
        states = ['RUNNING', 'QUEUED', 'DOWNLOADING', 'DONE', 'PENDING', 'FAILED']
        tasks.save_tasks([self.task(str(i), s) for i, s in enumerate(states)])
        api.handle_post(Handler(), '/api/reset_failed', {})
        self.assertEqual([t['status'] for t in tasks.load_tasks()], states[:-1] + ['PENDING'])

    def test_regenerate_keeps_inputs_and_rejects_second_request(self):
        tasks.save_tasks([self.task(status='DONE')])
        h = Handler({'task_id': '001'})
        api.handle_post(h, '/api/task/regenerate', {})
        self.assertTrue(h.result()['success'])
        self.assertEqual(tasks.load_tasks()[0]['template'], 'tpl.json')
        h = Handler({'task_id': '001'})
        api.handle_post(h, '/api/task/regenerate', {})
        self.assertEqual(h.status, 409)

    def test_active_delete_rejected(self):
        tasks.save_tasks([self.task(status='DOWNLOADING')])
        h = Handler()
        api.handle_post(h, '/api/delete_task', {'id': ['001']})
        self.assertEqual(h.status, 409)
        self.assertEqual(len(tasks.load_tasks()), 1)

    def test_clear_records_keeps_active_tasks_and_video(self):
        states = ['DONE', 'FAILED', 'PENDING', 'WAITING', 'QUEUED', 'RUNNING', 'DOWNLOADING']
        records = [self.task(str(i), state) for i, state in enumerate(states)]
        video = self.root / 'finished.mp4'
        video.write_bytes(b'keep this video')
        records[0]['output_file'] = str(video)
        tasks.save_tasks(records)
        h = Handler()
        api.handle_post(h, '/api/clear_task_records', {})
        self.assertEqual(h.result()['cleared'], 2)
        self.assertEqual([t['status'] for t in tasks.load_tasks()], states[2:])
        self.assertEqual(video.read_bytes(), b'keep this video')

    def test_add_after_clear_does_not_reuse_ids(self):
        tasks.save_tasks([self.task('050', 'DONE')])
        api.handle_post(Handler(), '/api/clear_task_records', {})
        self.assertEqual(tasks.load_tasks(), [])
        # 普通新增和批量新增均继续递增，序号持久化而非只留在内存。
        h = Handler({'template': 'tpl.json', 'model': 'model.jpg', 'apparel': 'sku.jpg'})
        api.handle_post(h, '/api/add_task', {})
        self.assertEqual(h.result()['tasks'][0]['task_id'], '051')
        h = Handler({'template': 'tpl.json', 'model': 'model.jpg', 'apparels': ['a.jpg', 'b.jpg']})
        api.handle_post(h, '/api/batch_add_tasks', {})
        self.assertEqual(h.result()['created_ids'], ['052', '053'])

    def test_empty_clear_is_idempotent(self):
        tasks.save_tasks([])
        for _ in range(2):
            h = Handler()
            api.handle_post(h, '/api/clear_task_records', {})
            self.assertEqual(h.result()['cleared'], 0)
            self.assertEqual(h.result()['tasks'], [])

    def test_claim_is_unique_under_concurrency(self):
        tasks.save_tasks([self.task(str(i)) for i in range(20)])
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(tasks.get_next_pending_task, ['worker'] * 30))
        ids = [r['task_id'] for r in results if r]
        self.assertEqual(len(ids), 20)
        self.assertEqual(len(set(ids)), 20)

    def test_failed_replace_preserves_csv(self):
        tasks.save_tasks([self.task()])
        original = Path(self.task_path).read_bytes()
        with patch.object(tasks.os, 'replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError): tasks.save_tasks([self.task(status='DONE')])
        self.assertEqual(Path(self.task_path).read_bytes(), original)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def matrix(self, model='model.jpg', template='tpl.json', scene=''):
        h = Handler()
        with patch.object(api, 'OUTPUT_DIR', str(self.root / 'output')), patch.object(api, 'get_dir_images', return_value=['sku.jpg']):
            api.handle_get(h, '/api/apparel_matrix_status', {'folder': ['ALL'], 'model': [model], 'template': [template], 'scene': [scene]})
        return h.result()['items'][0]

    def test_matrix_matches_full_combination(self):
        p = self.root / 'output/date/model/sku/001_tpl.mp4'
        p.parent.mkdir(parents=True); p.write_bytes(b'video')
        tasks.save_tasks([])
        self.assertEqual(self.matrix()['status'], 'DONE')
        self.assertEqual(self.matrix(model='other.jpg')['status'], 'UNPRODUCED')
        self.assertEqual(self.matrix(template='other.json')['status'], 'UNPRODUCED')
        self.assertEqual(self.matrix(scene='scene.jpg')['status'], 'UNPRODUCED')

    def test_matrix_queued_does_not_cross_models(self):
        tasks.save_tasks([self.task()])
        self.assertEqual(self.matrix()['status'], 'QUEUED')
        self.assertEqual(self.matrix(model='other.jpg')['status'], 'UNPRODUCED')

    def test_matrix_returns_actual_task_id(self):
        rel = 'date/model/sku/001_tpl.mp4'
        p = self.root / 'output' / rel
        p.parent.mkdir(parents=True); p.write_bytes(b'video')
        t = self.task(status='DONE'); t['output_file'] = rel
        tasks.save_tasks([t])
        self.assertEqual(self.matrix()['task_id'], '001')

    def test_thumbnail_extensions_do_not_collide(self):
        assets = self.root / 'assets'
        (assets / 'apparels').mkdir(parents=True)
        for name, color in [('sku.jpg', 'red'), ('sku.png', 'blue')]:
            Image.new('RGB', (32, 32), color).save(assets / 'apparels' / name)
        with patch.object(thumbs, 'ASSETS_DIR', str(assets)), patch.object(thumbs, 'THUMBS_DIR', str(assets / '.cache')):
            first = thumbs.get_thumbnail_path('apparels', 'sku.jpg')
            second = thumbs.get_thumbnail_path('apparels', 'sku.png')
            self.assertNotEqual(first, second)
            self.assertIsNone(thumbs.get_thumbnail_path('apparels', '../outside.jpg'))
            with Image.open(second) as image: self.assertGreater(image.getpixel((0, 0))[2], 200)

    def test_paths_reject_sibling_and_absolute_paths(self):
        root = str(self.root / 'output')
        for name in ['../output_backup/file.mp4', 'C:/file.mp4', '/file.mp4', '..\\outside.mp4']:
            self.assertIsNone(resolve_under(root, name))
        self.assertIsNotNone(resolve_under(root, 'date/model/file.mp4'))

    def test_select_final_output_even_if_history_order_differs(self):
        outputs = {'93': {'gifs': [{'filename': 'final.mp4'}, {'filename': 'final-audio.mp4'}]},
                   '16': {'gifs': [{'filename': 'preview.mp4'}]}}
        self.assertEqual(runner.select_video_output(outputs, '93')['filename'], 'final-audio.mp4')
        self.assertIsNone(runner.select_video_output(outputs, 'missing'))

    def test_download_failure_keeps_previous_video(self):
        p = self.root / 'video.mp4'; p.write_bytes(b'previous-video')
        response = io.BytesIO(b'x' * 2048); response.headers = {'Content-Length': '4096'}
        with patch.object(runner.urllib.request, 'urlopen', return_value=response):
            with self.assertRaises(IOError): runner.download_video('http://test', str(p))
        self.assertEqual(p.read_bytes(), b'previous-video')
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_download_success_replaces_video(self):
        p = self.root / 'video.mp4'; p.write_bytes(b'old')
        response = io.BytesIO(b'x' * 2048); response.headers = {'Content-Length': '2048'}
        with patch.object(runner.urllib.request, 'urlopen', return_value=response) as network:
            runner.download_video('http://test', str(p))
        self.assertEqual(len(p.read_bytes()), 2048)
        self.assertEqual(network.call_args.kwargs['timeout'], 30)

    def test_runner_downloads_only_final_node(self):
        wf = {'18': {'class_type': 'LoadImage', 'inputs': {'image': 'model.jpg'}},
              '35': {'class_type': 'LoadImage', 'inputs': {'image': 'sku.jpg'}},
              '16': {'class_type': 'VHS_VideoCombine', 'inputs': {'save_output': True}},
              '93': {'class_type': 'VHS_VideoCombine', 'inputs': {'save_output': True}}}
        (self.root / 'tpl.json').write_text(json.dumps(wf), encoding='utf-8')
        tasks.save_tasks([self.task(status='QUEUED')])
        def response(data):
            return io.BytesIO(json.dumps(data).encode())
        history = {'pid': {'status': {'status_str': 'success'}, 'outputs': {
            '93': {'gifs': [{'filename': 'final-audio.mp4', 'type': 'output'}]},
            '16': {'gifs': [{'filename': 'preview.mp4', 'type': 'temp'}]}}}}
        with patch.object(runner, 'TEMPLATES_DIR', str(self.root)), \
             patch.object(runner, 'OUTPUT_DIR', str(self.root / 'output')), \
             patch.object(runner, 'resolve_cloud_image_path', side_effect=lambda s, c, n: c + '/' + n), \
             patch.object(runner.time, 'sleep'), \
             patch.object(runner.urllib.request, 'urlopen', side_effect=[response({'prompt_id': 'pid'}), response(history)]), \
             patch.object(runner, 'download_video') as download:
            self.assertTrue(runner.execute_task_on_server({'url': 'http://fake', 'name': 'test'}, self.task()))
        self.assertEqual(download.call_count, 1)
        self.assertIn('final-audio.mp4', download.call_args.args[0])
        self.assertEqual(tasks.load_tasks()[0]['status'], 'DONE')

    def test_bind_failure_does_not_start_workers(self):
        from server import main
        with patch.object(main, 'ThreadingHTTPServer', side_effect=OSError('port busy')), \
             patch.object(main, 'start_dispatcher_daemon') as dispatcher, \
             patch.object(main, 'start_cluster_monitor') as monitor:
            with self.assertRaises(OSError): main.main()
        dispatcher.assert_not_called()
        monitor.assert_not_called()

    def test_video_ranges_and_literal_percent_filename(self):
        p = self.root / '100%25.mp4'; p.write_bytes(b'0123456789')
        with patch.object(static, 'OUTPUT_DIR', str(self.root)):
            for range_value, status, body in [('bytes=-3', 206, b'789'), ('bytes=4-99', 206, b'456789'), ('bytes=99-', 416, b'')]:
                h = Handler(headers={'Range': range_value})
                self.assertTrue(static.serve_video_stream(h, {'filename': [p.name]}))
                self.assertEqual(h.status, status)
                self.assertEqual(h.wfile.getvalue(), body)


if __name__ == '__main__':
    unittest.main()
