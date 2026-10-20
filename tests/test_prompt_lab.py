import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from PIL import Image
from server.services import prompt_lab as lab
from server.routes import api_router
from test_bugfixes import Handler


class PromptLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        original = (lab.ROOT / 'workflow.json').read_bytes()
        original_768p = (lab.ROOT / 'workflow_768p.json').read_bytes()
        root = Path(self.tmp.name)
        for obj, key, value in [(lab, 'ROOT', root), (lab, '_initialized', False),
                                (lab, '_enabled', False), (lab, '_thread', None),
                                (lab, '_notice', ''), (lab, '_threads', {}), (lab, '_workers', {}), (lab, '_running_ids', set()), (lab, '_starting', False), (lab, '_generation', 0), (lab.config, 'OUTPUT_DIR', str(root / 'output'))]:
            p = patch.object(obj, key, value)
            p.start(); self.addCleanup(p.stop)
        (root / 'workflow.json').write_bytes(original)
        (root / 'workflow_768p.json').write_bytes(original_768p)
        buf = io.BytesIO(); Image.new('RGB', (20, 30), 'red').save(buf, 'PNG')
        self.asset = lab.upload_asset({'name': '人物.png', 'data': base64.b64encode(buf.getvalue()).decode()})
        self.body = {'request_id': 'request-12345', 'assets': {'model': self.asset, 'apparel': self.asset},
                     'tasks': [{'prompt': '任务一\n镜头：人物转身，保留引号 "测试"。', 'seconds': 15},
                               {'prompt': '任务二：走近镜头', 'seconds': 8.5}]}
        self.srv = {'id': 'test', 'name': '测试节点', 'url': 'http://invalid'}
        cfg = patch.object(lab.config, 'load_server_config', return_value={'servers': [self.srv]})
        cfg.start(); self.addCleanup(cfg.stop)

    def enqueue(self):
        lab.enqueue(self.body)
        return lab.read_store()['tasks']

    def test_blank_prompt_and_invalid_seconds_rejected_atomically(self):
        for prompt, seconds in [('', 15), ('ok', 0), ('ok', 31), ('ok', float('nan')), ('ok', True), ('ok', 1.25)]:
            body = copy.deepcopy(self.body)
            body['tasks'][1] = {'prompt': prompt, 'seconds': seconds}
            with self.assertRaises(ValueError): lab.enqueue(body)
            self.assertEqual(lab.read_store()['tasks'], [])

    def test_enqueue_persists_multiline_and_is_idempotent_paused(self):
        result = lab.enqueue(self.body)
        self.assertEqual(lab.enqueue(self.body), result)
        tasks = lab.read_store()['tasks']
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]['prompt'], self.body['tasks'][0]['prompt'])
        self.assertTrue(all(t['status'] == 'PENDING' for t in tasks))
        self.assertFalse(lab.busy())

    def test_workflow_substitution_scene_optional_source_unchanged(self):
        task = self.enqueue()[0]
        original = (lab.ROOT / 'workflow.json').read_bytes()
        wf = lab.build_workflow(task, {'model': 'a.png', 'apparel': 'b.png'})
        self.assertEqual(wf['7']['inputs']['prompt'], task['prompt'])
        self.assertEqual(wf['7']['inputs']['seconds'], 15)
        self.assertEqual(wf['39']['inputs']['image'], 'a.png')
        self.assertEqual(wf['291']['inputs']['image'], 'b.png')
        self.assertNotIn('media_3', wf['7']['inputs'])
        scene = lab.build_workflow(task, {'model': 'a.png', 'apparel': 'b.png', 'scene': 'c.png'})
        self.assertEqual(scene['7']['inputs']['media_3'], ['292', 0])
        self.assertEqual(scene['292']['inputs']['image'], 'c.png')
        self.assertEqual(scene['9'], json.loads(original)['9'])
        self.assertEqual((lab.ROOT / 'workflow.json').read_bytes(), original)

    def test_768p_workflow_uses_second_pass_and_optional_scene(self):
        task = self.enqueue()[0]
        task['quality'] = '768p'
        source = (lab.ROOT / 'workflow_768p.json').read_bytes()
        wf = lab.build_workflow(task, {'model': 'person.png', 'apparel': 'coat.png'})
        self.assertEqual(wf['7']['inputs']['prompt'], task['prompt'])
        self.assertEqual(wf['7']['inputs']['seconds'], task['seconds'])
        self.assertEqual(wf['39']['inputs']['image'], 'person.png')
        self.assertEqual(wf['291']['inputs']['image'], 'coat.png')
        self.assertNotIn('media_3', wf['7']['inputs'])
        self.assertNotIn('292', wf)
        self.assertFalse(wf['16']['inputs']['save_output'])
        self.assertTrue(wf['93']['inputs']['save_output'])
        self.assertTrue(wf['93']['inputs']['filename_prefix'].startswith(task['task_id']))
        self.assertNotEqual(wf['60']['inputs']['noise_seed'], json.loads(source)['60']['inputs']['noise_seed'])
        scene = lab.build_workflow(task, {'model': 'person.png', 'apparel': 'coat.png', 'scene': 'room.png'})
        self.assertEqual(scene['292']['inputs']['image'], 'room.png')
        self.assertEqual(scene['7']['inputs']['media_3'], ['292', 0])
        self.assertEqual((lab.ROOT / 'workflow_768p.json').read_bytes(), source)

    def test_768p_enqueue_requires_own_prompt_duration_and_assets(self):
        source = self.enqueue()[0]
        body = {'request_id': 'production-request-12345', 'quality': '768p',
                'assets': {'model': self.asset, 'apparel': self.asset, 'scene': self.asset},
                'tasks': [{'prompt': '新成品提示词', 'seconds': 18}]}
        result = lab.enqueue(body)
        self.assertEqual(lab.enqueue(body), result)
        self.assertEqual(result['created_ids'], ['LAB-000003'])
        tasks = lab.read_store()['tasks']
        self.assertEqual(len(tasks), 3)
        final = tasks[2]
        self.assertEqual(final['quality'], '768p')
        self.assertEqual(final['prompt'], '新成品提示词')
        self.assertEqual(final['seconds'], 18)
        self.assertEqual(final['assets']['scene'], self.asset)
        self.assertNotIn('source_task_id', final)
        self.assertEqual(final['status'], 'PENDING')
        self.assertEqual(tasks[0]['prompt'], source['prompt'])
        self.assertEqual(tasks[0]['status'], 'PENDING')

    def test_768p_enqueue_api_uses_explicit_form_values(self):
        self.enqueue()
        handler = Handler({'request_id': 'production-api-12345', 'quality': '768p',
                           'assets': {'model': self.asset, 'apparel': self.asset},
                           'tasks': [{'prompt': '独立的成品文案', 'seconds': 12}]})
        api_router.handle_post(handler, '/api/prompt_lab/enqueue', {})
        result = handler.result()
        self.assertTrue(result['success'])
        self.assertEqual(result['created_ids'], ['LAB-000003'])
        final = lab.read_store()['tasks'][2]
        self.assertEqual((final['quality'], final['prompt'], final['seconds']),
                         ('768p', '独立的成品文案', 12))

    def test_768p_receives_only_second_pass_video(self):
        task = self.enqueue()[0]
        task['quality'] = '768p'
        lab.update(task['task_id'], quality='768p')
        def remote(url, payload=None):
            if url.endswith('/prompt'):
                self.assertEqual(payload['prompt']['7']['inputs']['prompt'], task['prompt'])
                return {'prompt_id': 'cloud-768p'}
            return {'cloud-768p': {'status': {'status_str': 'success'}, 'outputs': {
                '16': {'gifs': [{'filename': 'preview-480.mp4', 'type': 'output'}]},
                '93': {'gifs': [{'filename': 'final-768.mp4', 'type': 'output'}]}}}}
        def download(url, path): Path(path).write_bytes(b'final video' * 1000)
        with patch.object(lab, 'upload_remote', return_value='prompt_lab/img.png'), \
             patch.object(lab, 'remote_json', side_effect=remote), \
             patch.object(lab, 'download_video', side_effect=download) as received:
            lab.execute(task, self.srv)
        self.assertIn('filename=final-768.mp4', received.call_args.args[0])
        self.assertNotIn('preview-480.mp4', received.call_args.args[0])
        finished = lab.read_store()['tasks'][0]
        self.assertEqual(finished['status'], 'DONE')
        self.assertTrue(finished['output_file'].startswith('prompt_lab/768p/'))
        self.assertTrue((Path(lab.config.OUTPUT_DIR) / finished['output_file']).exists())

    def test_upload_retries_connection_loss_without_submitting_video(self):
        response = io.BytesIO(b'{"name":"uploaded.png","subfolder":"prompt_lab","type":"input"}')
        with patch.object(lab.urllib.request, 'urlopen', side_effect=[urllib.error.URLError('connection reset'), response]) as request, \
             patch.object(lab.time, 'sleep', return_value=None):
            uploaded = lab.upload_remote('http://mock-server', self.asset)
        self.assertEqual(uploaded, 'prompt_lab/uploaded.png')
        self.assertEqual(request.call_count, 2)
        self.assertTrue(all(call.args[0].full_url.endswith('/upload/image') for call in request.call_args_list))

    def test_pause_finishes_claimed_task_but_does_not_claim_next(self):
        self.enqueue(); lab._enabled = True
        def execute(t, srv):
            lab.pause()
            lab.update(t['task_id'], status='DONE', output_file='result.mp4')
        with patch.object(lab, 'execute', side_effect=execute) as execute_mock:
            lab.worker(self.srv)
        self.assertEqual(execute_mock.call_count, 1)
        self.assertEqual([t['status'] for t in lab.read_store()['tasks']], ['DONE', 'PENDING'])

    def test_serial_worker_runs_in_order(self):
        self.enqueue(); lab._enabled = True; order = []
        def execute(t, srv):
            order.append((t['task_id'], t['seconds']))
            lab.update(t['task_id'], status='DONE')
        with patch.object(lab, 'execute', side_effect=execute): lab.worker(self.srv)
        self.assertEqual(order, [('LAB-000001', 15), ('LAB-000002', 8.5)])
        self.assertFalse(lab._enabled)

    def test_lost_submit_response_stops_without_resubmitting(self):
        self.enqueue(); lab._enabled = True
        def execute(t, srv):
            lab.update(t['task_id'], status='SUBMITTING')
            raise TimeoutError('lost response')
        with patch.object(lab, 'execute', side_effect=execute) as call: lab.worker(self.srv)
        self.assertEqual(call.call_count, 1)
        self.assertEqual([t['status'] for t in lab.read_store()['tasks']], ['REVIEW', 'PENDING'])

    def test_restart_preserves_prompt_id_without_requeue(self):
        task = self.enqueue()[0]
        lab.update(task['task_id'], status='RUNNING', prompt_id='persisted-id')
        lab._initialized = False; lab.initialize()
        task = lab.read_store()['tasks'][0]
        self.assertEqual(task['status'], 'REVIEW')
        self.assertEqual(task['prompt_id'], 'persisted-id')
        self.assertFalse(lab.busy())

    def test_explicit_cloud_error_is_failed_not_ambiguous(self):
        self.enqueue(); lab._enabled = True
        def execute(t, srv):
            lab.update(t['task_id'], status='RUNNING', prompt_id='known')
            raise lab.RemoteExecutionError('model execution failed')
        with patch.object(lab, 'execute', side_effect=execute): lab.worker(self.srv)
        self.assertEqual(lab.read_store()['tasks'][0]['status'], 'FAILED')
        self.assertFalse(lab._enabled)

    def test_cloud_seconds_limits_checked_before_start(self):
        tasks = self.enqueue()
        info = {'MiniMaxH3Easy': {'input': {'required': {'seconds': ['FLOAT', {'min': 1, 'max': 10}]}}}}
        with patch.object(lab, 'remote_json', return_value=info):
            with self.assertRaises(ValueError): lab.preflight('http://invalid', tasks)

    def test_api_upload_and_enqueue_return_errors_without_mutation(self):
        handler = Handler({'request_id': 'test-request', 'tasks': []})
        api_router.handle_post(handler, '/api/prompt_lab/enqueue', {})
        self.assertFalse(handler.result()['success'])
        self.assertEqual(lab.read_store()['tasks'], [])

    def test_retry_preserves_prompt_assets_seconds_and_output(self):
        task = self.enqueue()[0]
        lab.update(task['task_id'], status='DONE', output_file='old.mp4')
        lab.task_action({'task_id': task['task_id'], 'action': 'retry'})
        new = lab.read_store()['tasks'][0]
        for field in ('prompt', 'seconds', 'assets'): self.assertEqual(task[field], new[field])
        self.assertEqual(new['status'], 'PENDING')
        self.assertEqual(new['output_file'], 'old.mp4')
        self.assertFalse(lab.busy())

    def test_delete_keeps_high_watermark_and_output(self):
        task = self.enqueue()[0]
        output = lab.ROOT / 'video.mp4'; output.write_bytes(b'keep')
        lab.task_action({'task_id': task['task_id'], 'action': 'delete'})
        self.body['request_id'] = 'request-67890'
        self.assertEqual(lab.enqueue(self.body)['created_ids'][0], 'LAB-000003')
        self.assertTrue(output.exists())

    def test_active_and_unconfirmed_review_mutation_rejected(self):
        task = self.enqueue()[0]
        for status in ['RUNNING', 'REVIEW']:
            lab.update(task['task_id'], status=status)
            with self.assertRaises(ValueError): lab.task_action({'task_id': task['task_id'], 'action': 'retry'})

    def test_delete_requires_pause_and_preserves_active_or_review_tasks(self):
        task = self.enqueue()[0]
        before = lab.read_store()
        for flag in ['_enabled', '_starting']:
            with patch.object(lab, flag, True):
                with self.assertRaisesRegex(ValueError, '暂停队列领取'):
                    lab.task_action({'task_id': task['task_id'], 'action': 'delete'})
            self.assertEqual(lab.read_store(), before)
        for status in [*lab.ACTIVE, 'REVIEW', 'UNKNOWN']:
            lab.update(task['task_id'], status=status)
            before = lab.read_store()
            with self.assertRaises(ValueError):
                lab.task_action({'task_id': task['task_id'], 'action': 'delete'})
            self.assertEqual(lab.read_store(), before)

    def test_paused_delete_ignores_other_review_task_and_retains_receipt(self):
        tasks = self.enqueue()
        lab.update(tasks[0]['task_id'], status='REVIEW', prompt_id='keep-receipt')
        before = lab.read_store()
        with patch.object(lab, 'busy', return_value=True):
            lab.task_action({'task_id': tasks[1]['task_id'], 'action': 'delete'})
        after = lab.read_store()
        self.assertEqual(after['tasks'], [before['tasks'][0]])
        self.assertEqual(after['sequence'], before['sequence'])
        self.assertEqual(after['requests'], before['requests'])

    def test_path_traversal_and_non_image_rejected(self):
        for name in ['../workflow.json', '..\\workflow.json', 'C:\\secret.png', '/secret.png']:
            with self.assertRaises(ValueError): lab.asset_path({'file': name})
        with self.assertRaises(Exception): lab.upload_asset({'data': base64.b64encode(b'not image').decode()})

    def test_saved_library_images_can_be_selected_without_copying(self):
        library = Path(self.tmp.name) / 'system_assets'
        for category in ('models', 'apparels', 'scenes'):
            (library / category).mkdir(parents=True)
        source = lab.asset_path(self.asset)
        for category in ('models', 'apparels', 'scenes'):
            (library / category / '选中的图片.png').write_bytes(source.read_bytes())
        with patch.object(lab.config, 'ASSETS_DIR', str(library)):
            handler = Handler()
            api_router.handle_get(handler, '/api/prompt_lab/library', {})
            self.assertEqual(handler.result()['models'], ['选中的图片.png'])
            self.assertEqual(handler.result()['apparels'], ['选中的图片.png'])
            self.assertEqual(handler.result()['scenes'], ['选中的图片.png'])
            refs = {role: {'source': 'library', 'category': category,
                           'file': '选中的图片.png', 'name': '选中的图片.png'}
                    for role, category in lab.LIBRARY_CATEGORIES.items()}
            body = {'request_id': 'saved-library-12345', 'quality': '768p', 'assets': refs,
                    'tasks': [{'prompt': '使用系统保存的素材', 'seconds': 15}]}
            self.assertEqual(lab.enqueue(body)['created_ids'], ['LAB-000001'])
            task = lab.read_store()['tasks'][0]
            self.assertEqual(task['assets'], refs)
            self.assertEqual(lab.asset_path(refs['apparel'], 'apparel'), library / 'apparels' / '选中的图片.png')
            self.assertEqual(len(list((lab.ROOT / 'assets').iterdir())), 1)
            wrong = copy.deepcopy(body); wrong['request_id'] = 'saved-library-67890'
            wrong['assets']['apparel']['category'] = 'models'
            with self.assertRaisesRegex(ValueError, '类别不匹配'):
                lab.enqueue(wrong)
            self.assertEqual(len(lab.read_store()['tasks']), 1)

    def test_large_saved_image_optimizes_upload_copy_without_touching_original(self):
        large = Path(self.tmp.name) / 'large.bmp'
        pixels = lab.os.urandom(2500 * 3000 * 3)
        Image.frombytes('RGB', (2500, 3000), pixels).save(large, 'BMP')
        before = large.stat().st_size
        self.assertGreater(before, 20 * 1024 * 1024)
        name, data = lab.remote_image_data(large)
        self.assertTrue(name.endswith('.webp'))
        self.assertLessEqual(len(data), 19 * 1024 * 1024)
        self.assertEqual(data[:4], b'RIFF')
        self.assertEqual(large.stat().st_size, before)

    def test_atomic_write_failure_preserves_tasks(self):
        self.enqueue(); before = (lab.ROOT / 'tasks.json').read_bytes()
        with patch.object(lab.os, 'replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError): lab.update('LAB-000001', status='DONE')
        self.assertEqual((lab.ROOT / 'tasks.json').read_bytes(), before)

    def test_original_start_blocked_while_lab_busy(self):
        lab._enabled = True
        with patch.object(api_router, 'run_multi_worker_batch') as start:
            handler = Handler(); api_router.handle_post(handler, '/api/start_batch', {})
        start.assert_not_called()
        self.assertFalse(handler.result()['success'])

    def test_lab_start_blocked_by_original_active_tasks(self):
        self.enqueue()
        with patch.object(lab, 'load_tasks', return_value=[{'status': 'RUNNING'}]):
            with self.assertRaises(ValueError): lab.start('test')

    def test_execute_upload_submit_receive_final_node_only(self):
        task = self.enqueue()[0]; posts = []
        def remote(url, payload=None):
            if url.endswith('/prompt'):
                posts.append(payload)
                return {'prompt_id': 'cloud-id'}
            return {'cloud-id': {'status': {'status_str': 'success'}, 'outputs': {
                'other': {'gifs': [{'filename': 'wrong.mp4'}]},
                '16': {'gifs': [{'filename': 'final.mp4', 'subfolder': 'clips', 'type': 'output'}]}}}}
        def download(url, path): Path(path).write_bytes(b'video' * 1000)
        with patch.object(lab, 'upload_remote', return_value='prompt_lab/img.png') as uploads, \
             patch.object(lab, 'remote_json', side_effect=remote), \
             patch.object(lab, 'download_video', side_effect=download) as downloads:
            lab.execute(task, self.srv)
        self.assertEqual(uploads.call_count, 2)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['prompt']['7']['inputs']['seconds'], 15)
        self.assertIn('filename=final.mp4', downloads.call_args.args[0])
        current = lab.read_store()['tasks'][0]
        self.assertEqual(current['status'], 'DONE')
        self.assertEqual(current['prompt_id'], 'cloud-id')
        self.assertTrue((Path(lab.config.OUTPUT_DIR) / current['output_file']).exists())


if __name__ == '__main__': unittest.main()
