"""Manual start/pause regression tests; temporary CSV, no cloud or real workers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_bugfixes import Handler
from server.routes import api_router as api
from server.services import task_service as tasks
from server.workers import batch_dispatcher as dispatch
from server import config


class ManualDispatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for obj, key, value in [
            (tasks, 'TASK_FILE', str(Path(tmp.name) / 'tasks.csv')),
            (dispatch, 'cluster_paused', True), (config, 'batch_is_active', False),
            (dispatch, 'log_msg', lambda *a: None), (api, 'log_msg', lambda *a: None),
        ]:
            p = patch.object(obj, key, value); p.start(); self.addCleanup(p.stop)
        tasks.save_tasks([])

    def add(self):
        api.handle_post(Handler({'template':'tpl.json','model':'model.jpg','apparel':'sku.jpg'}), '/api/add_task', {})

    def test_initial_state_is_paused_in_fresh_process(self):
        import subprocess, sys
        result = subprocess.run([sys.executable, '-c',
            'from server.workers import batch_dispatcher as d; from server import config; assert d.cluster_paused and not config.batch_is_active'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_add_and_batch_add_wait_for_explicit_start(self):
        self.add()
        api.handle_post(Handler({'template':'tpl.json','model':'model.jpg','apparels':['b.jpg']}), '/api/batch_add_tasks', {})
        self.assertIsNone(dispatch.claim_next_task('test'))
        self.assertEqual([t['status'] for t in tasks.load_tasks()], ['PENDING','PENDING'])
        self.assertFalse(config.batch_is_active)

    def test_start_pause_and_resume_gate_claims(self):
        self.add(); self.add()
        with patch.object(dispatch,'start_dispatcher_daemon'), patch.object(dispatch,'sync_cluster_workers'):
            api.handle_post(Handler(), '/api/start_batch', {})
            self.assertTrue(config.batch_is_active)
            first = dispatch.claim_next_task('test')
            self.assertIsNotNone(first)
            api.handle_post(Handler(), '/api/stop_batch', {})
            self.assertFalse(config.batch_is_active)
            self.assertIsNone(dispatch.claim_next_task('test'))
            self.assertEqual(tasks.load_tasks()[0]['status'], 'QUEUED')
            api.handle_post(Handler(), '/api/start_batch', {})
            second = dispatch.claim_next_task('test')
            self.assertNotEqual(first['task_id'], second['task_id'])

    def test_retry_reset_and_enable_do_not_start_dispatch(self):
        self.add()
        record=tasks.load_tasks()[0]
        cfg={'servers':[{'id':'test','enabled':True,'url':'http://invalid'}]}
        with patch.object(api,'load_server_config',return_value=cfg), \
             patch.object(api,'run_multi_worker_batch') as start, \
             patch.object(api,'save_server_config'), patch.object(api,'sync_cluster_workers'), \
             patch.object(api.threading,'Thread') as thread:
            record['status']='DONE';tasks.save_tasks([record])
            api.handle_post(Handler({'task_id':record['task_id']}),'/api/task/regenerate',{})
            record['status']='FAILED';tasks.save_tasks([record])
            api.handle_post(Handler(),'/api/reset_failed',{})
            api.handle_post(Handler({'id':'test','enabled':True}),'/api/server/toggle',{})
            start.assert_not_called()
            self.assertTrue(all(call.kwargs.get('target') is not start for call in thread.call_args_list))
        self.assertIsNone(dispatch.claim_next_task('test'))
        self.assertFalse(config.batch_is_active)

    def test_worker_waits_even_when_woken_with_pending_task(self):
        self.add()
        enabled={'servers':[{'id':'test','enabled':True}]}
        with patch.object(dispatch,'load_server_config',side_effect=[enabled,enabled,{'servers':[]},{'servers':[]}]), \
             patch.object(dispatch._cluster_wake_event,'wait'), \
             patch.object(dispatch,'execute_task_on_server') as execute:
            dispatch.worker_thread_loop('test')
        execute.assert_not_called()
        self.assertEqual(tasks.load_tasks()[0]['status'],'PENDING')

if __name__=='__main__': unittest.main()
