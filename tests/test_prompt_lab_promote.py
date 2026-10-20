import copy
import threading
import unittest
from unittest.mock import patch
import test_prompt_lab
from server.services import prompt_lab as lab
from server.routes import api_router
from test_bugfixes import Handler


class PromotionTests(unittest.TestCase):
    setUp = test_prompt_lab.PromptLabTests.setUp
    enqueue = test_prompt_lab.PromptLabTests.enqueue

    def source(self, scene=False):
        if scene:
            self.body['assets']['scene'] = self.asset
        task = self.enqueue()[0]
        return lab.update(task['task_id'], status='DONE', output_file='test.mp4', prompt_id='test-receipt')

    def test_exact_snapshot_and_no_execution(self):
        for scene in (False, True):
            with self.subTest(scene=scene):
                source = self.source(scene) if not scene else lab.update('LAB-000002', status='DONE', output_file='2.mp4', assets={'model':self.asset,'apparel':self.asset,'scene':self.asset})
                before = copy.deepcopy(source)
                with patch.object(lab, 'worker') as worker:
                    result = lab.promote({'task_id':source['task_id'], 'prompt':'WRONG', 'assets':{}})
                worker.assert_not_called()
                target = next(t for t in lab.read_store()['tasks'] if t['task_id']==result['task_id'])
                for key in ('prompt','seconds','assets'):
                    self.assertEqual(target[key], before[key])
                self.assertEqual(target['status'], 'PENDING')
                self.assertEqual(target['quality'], '768p')
                self.assertTrue(target['held_for_start'])
                self.assertEqual(target['prompt_id'], '')
                self.assertEqual(target['source_task_id'], source['task_id'])
                self.assertFalse(lab.busy())

    def test_concurrent_duplicate_and_deleted_target_do_not_duplicate(self):
        source=self.source();results=[]
        threads=[threading.Thread(target=lambda:results.append(lab.promote({'task_id':source['task_id']}))) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join(3)
        self.assertEqual(len({r['task_id'] for r in results}),1)
        tid=results[0]['task_id'];lab.task_action({'task_id':tid,'action':'delete'})
        self.assertTrue(lab.promote({'task_id':source['task_id']})['already_added'])
        self.assertEqual(len(lab.read_store()['tasks']),2)

    def test_invalid_source_and_missing_assets_are_atomic(self):
        source=self.source()
        for fields in ({'status':'RUNNING'},{'quality':'768p'},{'assets':{}},{'output_file':''}):
            data=lab.read_store();data['tasks'][0]=dict(source,**fields);lab.save_store(data)
            original=lab.read_store()
            with self.assertRaises(ValueError):lab.promote({'task_id':source['task_id']})
            self.assertEqual(lab.read_store(),original)

    def test_storage_failure_does_not_leave_link_or_consume_sequence(self):
        source=self.source();before=lab.read_store()
        with patch.object(lab,'save_store',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):lab.promote({'task_id':source['task_id']})
        self.assertEqual(lab.read_store(),before)

    def test_promotion_held_until_explicit_start(self):
        source=self.source();tid=lab.promote({'task_id':source['task_id']})['task_id']
        lab._enabled=True
        self.assertIsNone(lab.claim(self.srv,'768p'))
        lab._enabled=False
        seen=[]
        def execute(task,srv):seen.append(task['task_id']);lab.update(task['task_id'],status='DONE')
        with patch.object(lab,'load_tasks',return_value=[]),patch.object(lab,'preflight'),patch.object(lab,'remote_json',return_value={}),patch.object(lab,'execute',side_effect=execute):
            lab.start('test','768p')
            for thread in lab._threads.values():thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(seen,[tid])
        self.assertEqual(lab.read_store()['tasks'][1]['status'],'PENDING')

    def test_api_returns_target_and_survives_reinitialization(self):
        source=self.source();handler=Handler({'task_id':source['task_id']})
        api_router.handle_post(handler,'/api/prompt_lab/promote',{})
        result=handler.result();self.assertTrue(result['success'])
        lab._initialized=False;lab.initialize()
        self.assertEqual(lab.promote({'task_id':source['task_id']})['task_id'],result['task_id'])

    def test_promotion_added_during_start_check_waits_for_next_manual_start(self):
        source=self.source();first=lab.promote({'task_id':source['task_id']})['task_id']
        lab.update('LAB-000002',status='DONE',output_file='second.mp4')
        late=[];seen=[]
        def preflight(*args):late.append(lab.promote({'task_id':'LAB-000002'})['task_id'])
        def execute(task,srv):seen.append(task['task_id']);lab.update(task['task_id'],status='DONE')
        with patch.object(lab,'load_tasks',return_value=[]),patch.object(lab,'preflight',side_effect=preflight),patch.object(lab,'remote_json',return_value={}),patch.object(lab,'execute',side_effect=execute):
            lab.start('test','768p')
            for thread in lab._threads.values():thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(seen,[first])
        target=next(t for t in lab.read_store()['tasks'] if t['task_id']==late[0])
        self.assertEqual(target['status'],'PENDING');self.assertTrue(target['held_for_start'])
