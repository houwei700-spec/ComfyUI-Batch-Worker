import threading
import unittest
import urllib.error
from unittest.mock import patch
import test_prompt_lab
from server.services import prompt_lab as lab


class ParallelTests(unittest.TestCase):
    setUp = test_prompt_lab.PromptLabTests.setUp
    enqueue = test_prompt_lab.PromptLabTests.enqueue
    def setup_servers(self):
        return [{'id': str(i), 'name': 'node-'+str(i), 'url': 'http://mock-'+str(i), 'enabled': True} for i in range(3)]

    def finish(self):
        for thread in list(lab._threads.values()):
            thread.join(8)
            self.assertFalse(thread.is_alive(), 'worker did not exit')
        self.assertFalse(lab.busy())

    def test_three_servers_claim_unique_tasks_and_drain(self):
        servers = self.setup_servers()
        self.body['tasks'] = [{'prompt': str(i), 'seconds': 15} for i in range(30)]
        self.enqueue(); barrier = threading.Barrier(3); seen=[]; used=set(); guard=threading.Lock()
        def execute(task, srv):
            with guard:
                first = srv['id'] not in used
                used.add(srv['id']);seen.append(task['task_id'])
            if first: barrier.wait(5)
            lab.update(task['task_id'], status='DONE')
        with patch.object(lab.config, 'load_server_config', return_value={'servers':servers}), \
             patch.object(lab, 'load_tasks', return_value=[]), \
             patch.object(lab, 'preflight'), patch.object(lab, 'remote_json', return_value={}), \
             patch.object(lab, 'execute', side_effect=execute):
            lab.start('all'); self.finish()
        self.assertEqual(len(seen),30);self.assertEqual(len(set(seen)),30)
        self.assertEqual(used,{'0','1','2'})
        self.assertTrue(all(t['status']=='DONE' for t in lab.read_store()['tasks']))

    def test_starting_768p_lane_leaves_480p_tasks_pending(self):
        self.enqueue()
        lab.enqueue({'request_id': 'production-lane-12345', 'quality': '768p',
                     'assets': {'model': self.asset, 'apparel': self.asset},
                     'tasks': [{'prompt': '独立成品', 'seconds': 12}]})
        seen=[]
        def execute(task, srv):
            seen.append((task['task_id'], task['quality']))
            lab.update(task['task_id'], status='DONE')
        with patch.object(lab, 'load_tasks', return_value=[]), \
             patch.object(lab, 'preflight'), \
             patch.object(lab, 'remote_json', return_value={}), \
             patch.object(lab, 'execute', side_effect=execute):
            lab.start('test', '768p'); self.finish()
        self.assertEqual(seen, [('LAB-000003', '768p')])
        self.assertEqual([t['status'] for t in lab.read_store()['tasks']],
                         ['PENDING', 'PENDING', 'DONE'])

    def test_pause_all_servers_preserves_inflight_completion(self):
        servers=self.setup_servers(); self.body['tasks']=[{'prompt':str(i),'seconds':15} for i in range(8)]
        self.enqueue(); barrier=threading.Barrier(4);release=threading.Event()
        def execute(task,srv):
            barrier.wait(5);release.wait(5);lab.update(task['task_id'],status='DONE')
        with patch.object(lab.config,'load_server_config',return_value={'servers':servers}), \
             patch.object(lab,'load_tasks',return_value=[]), patch.object(lab,'preflight'), \
             patch.object(lab,'remote_json',return_value={}), patch.object(lab,'execute',side_effect=execute):
            lab.start();barrier.wait(5);lab.pause()
            self.assertTrue(lab.busy())
            self.assertIsNone(lab.claim(servers[0]));release.set();self.finish()
        rows=lab.read_store()['tasks']
        self.assertEqual(sum(t['status']=='DONE' for t in rows),3)
        self.assertEqual(sum(t['status']=='PENDING' for t in rows),5)

    def test_offline_and_duplicate_servers_are_skipped(self):
        servers=self.setup_servers();servers.append(dict(servers[0],id='alias'))
        self.enqueue();used=[]
        def preflight(url,tasks):
            if url.endswith('1'):raise OSError('offline')
        def execute(task,srv):used.append(srv['id']);lab.update(task['task_id'],status='DONE')
        with patch.object(lab.config,'load_server_config',return_value={'servers':servers}), \
             patch.object(lab,'load_tasks',return_value=[]),patch.object(lab,'preflight',side_effect=preflight) as checks, \
             patch.object(lab,'remote_json',return_value={}),patch.object(lab,'execute',side_effect=execute):
            lab.start('all');self.finish()
        self.assertEqual(checks.call_count,3);self.assertNotIn('1',used)
        self.assertEqual(lab._workers['1']['state'],'UNAVAILABLE')
        self.assertEqual(len(lab._threads),2)

    def test_paused_delete_pending_while_other_worker_receives(self):
        for quality in ['480p', '768p']:
            with self.subTest(quality=quality):
                self.body['request_id'] = 'paused-delete-' + quality
                self.body['quality'] = quality
                ids = lab.enqueue(self.body)['created_ids']
                entered = threading.Event(); release = threading.Event(); seen = []
                def execute(task, srv):
                    seen.append(task['task_id'])
                    lab.update(task['task_id'], status='DOWNLOADING', prompt_id='keep-original')
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError('test did not release worker')
                    lab.update(task['task_id'], status='DONE', output_file='keep.mp4')
                with patch.object(lab, 'load_tasks', return_value=[]), \
                     patch.object(lab, 'preflight'), \
                     patch.object(lab, 'remote_json', return_value={}), \
                     patch.object(lab, 'execute', side_effect=execute):
                    lab.start('test', quality)
                    try:
                        self.assertTrue(entered.wait(5))
                        lab.pause()
                        self.assertTrue(lab.busy())
                        lab.task_action({'task_id': ids[1], 'action': 'delete'})
                        with self.assertRaises(ValueError):
                            lab.task_action({'task_id': ids[0], 'action': 'delete'})
                        self.assertIsNone(lab.claim(self.srv, quality))
                    finally:
                        release.set(); self.finish()
                rows = {t['task_id']: t for t in lab.read_store()['tasks']}
                self.assertNotIn(ids[1], rows)
                self.assertEqual(seen, [ids[0]])
                self.assertEqual(rows[ids[0]]['status'], 'DONE')
                self.assertEqual(rows[ids[0]]['prompt_id'], 'keep-original')
                self.assertEqual(rows[ids[0]]['output_file'], 'keep.mp4')

    def test_server_failure_does_not_stop_other_servers(self):
        servers=self.setup_servers();self.body['tasks']=[{'prompt':str(i),'seconds':15} for i in range(8)]
        self.enqueue();barrier=threading.Barrier(3);used=set();guard=threading.Lock()
        def execute(task,srv):
            with guard:first=srv['id'] not in used;used.add(srv['id'])
            if first:barrier.wait(5)
            if srv['id']=='0':raise ValueError('upload failed')
            lab.update(task['task_id'],status='DONE')
        with patch.object(lab.config,'load_server_config',return_value={'servers':servers}), \
             patch.object(lab,'load_tasks',return_value=[]),patch.object(lab,'preflight'), \
             patch.object(lab,'remote_json',return_value={}),patch.object(lab,'execute',side_effect=execute):
            lab.start();self.finish()
        rows=lab.read_store()['tasks']
        self.assertEqual(sum(t['status']=='FAILED' for t in rows),1)
        self.assertEqual(sum(t['status']=='DONE' for t in rows),7)

    def test_pause_cancels_preflight_without_claims(self):
        self.enqueue();entered=threading.Event();release=threading.Event();errors=[]
        def preflight(*args):entered.set();release.wait(5)
        def start():
            try:lab.start('test')
            except ValueError as e:errors.append(str(e))
        with patch.object(lab,'load_tasks',return_value=[]),patch.object(lab,'preflight',side_effect=preflight), \
             patch.object(lab,'remote_json',return_value={}):
            thread=threading.Thread(target=start);thread.start();self.assertTrue(entered.wait(5))
            lab.pause();release.set();thread.join(6)
        self.assertTrue(errors);self.assertFalse(lab.busy())
        self.assertTrue(all(t['status']=='PENDING' for t in lab.read_store()['tasks']))

    def test_transient_preflight_disconnect_recovers_without_duplicate_claim(self):
        self.enqueue()
        seen=[]
        def preflight(*args):
            if not seen:
                seen.append('disconnect')
                raise urllib.error.URLError('temporary TLS disconnect')
            seen.append('connected')
        def execute(task, srv):
            seen.append(task['task_id'])
            lab.update(task['task_id'], status='DONE')
        with patch.object(lab, 'load_tasks', return_value=[]), \
             patch.object(lab, 'preflight', side_effect=preflight), \
             patch.object(lab, 'remote_json', return_value={'queue_running': [], 'queue_pending': []}), \
             patch.object(lab, 'execute', side_effect=execute), \
             patch.object(lab.time, 'sleep', return_value=None):
            lab.start('test'); self.finish()
        self.assertEqual(seen.count('disconnect'), 1)
        self.assertEqual(seen.count('connected'), 1)
        self.assertEqual(seen.count('LAB-000001'), 1)
        self.assertTrue(all(t['status']=='DONE' for t in lab.read_store()['tasks']))

    def test_repeated_preflight_disconnect_leaves_tasks_pending(self):
        self.enqueue()
        with patch.object(lab, 'load_tasks', return_value=[]), \
             patch.object(lab, 'preflight', side_effect=urllib.error.URLError('TLS disconnected')) as check, \
             patch.object(lab.time, 'sleep', return_value=None):
            with self.assertRaisesRegex(ValueError, '不可用'):
                lab.start('test')
        self.assertEqual(check.call_count, 3)
        self.assertTrue(all(t['status']=='PENDING' for t in lab.read_store()['tasks']))
        self.assertFalse(lab.busy())
