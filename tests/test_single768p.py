import copy
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from server.services import prompt_lab as lab
import test_prompt_lab as base_tests
import test_prompt_lab_promote as promotion_tests


class SinglePassTests(unittest.TestCase):
    enqueue = base_tests.PromptLabTests.enqueue
    source = promotion_tests.PromotionTests.source

    def setUp(self):
        template = (lab.ROOT / 'workflow_single768p.json').read_bytes()
        base_tests.PromptLabTests.setUp(self)
        (lab.ROOT / 'workflow_single768p.json').write_bytes(template)

    def test_build_routes_and_optional_scene_preserve_parameters(self):
        original = json.loads((lab.ROOT / 'workflow_single768p.json').read_text('utf-8'))
        for seconds in (8.5, 15, 30):
            for scene in (False, True):
                task = {'quality':'single768p','task_id':'CHECK','prompt':'自己的提示词','seconds':seconds}
                images = {'model':'model.png','apparel':'coat.png'}
                if scene: images['scene']='scene.png'
                wf = lab.build_workflow(task,images)
                self.assertEqual(wf['7']['inputs']['resolution'],'768P')
                self.assertEqual(wf['7']['inputs']['prompt'],task['prompt'])
                self.assertEqual(wf['7']['inputs']['seconds'],seconds)
                self.assertNotIn('93',wf)
                self.assertTrue(wf['16']['inputs']['save_output'])
                self.assertEqual('media_3' in wf['7']['inputs'],scene)
                self.assertEqual('292' in wf,scene)
                if scene:self.assertEqual(wf['292']['inputs']['image'],'scene.png')
                for node in ('6','9','13'):self.assertEqual(wf[node],original[node])
                for node in wf.values():
                    for value in node['inputs'].values():
                        if isinstance(value,list):self.assertIn(value[0],wf)

    def test_enqueue_and_start_only_selected_quality(self):
        self.enqueue()
        body=copy.deepcopy(self.body);body.update(quality='single768p',request_id='single-request-12345')
        ids=lab.enqueue(body)['created_ids']
        self.assertEqual(lab.enqueue(body)['created_ids'],ids)
        seen=[]
        def execute(task,srv):seen.append(task['task_id']);lab.update(task['task_id'],status='DONE')
        with patch.object(lab,'load_tasks',return_value=[]),patch.object(lab,'preflight'),patch.object(lab,'remote_json',return_value={}),patch.object(lab,'execute',side_effect=execute):
            lab.start('test','single768p')
            for t in lab._threads.values():t.join(5);self.assertFalse(t.is_alive())
        self.assertEqual(seen,ids)
        self.assertTrue(all(t['status']=='PENDING' for t in lab.read_store()['tasks'][:2]))

    def test_promote_each_target_once_concurrently_and_legacy_link(self):
        source=self.source(scene=True);results=[]
        def promote(q):results.append((q,lab.promote({'task_id':source['task_id'],'quality':q})))
        threads=[threading.Thread(target=promote,args=(q,)) for q in ('768p','single768p') for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join(5)
        self.assertEqual(len(results),8)
        for q in ('768p','single768p'):
            self.assertEqual(len({r['task_id'] for k,r in results if k==q}),1)
        tasks=lab.read_store()['tasks'];self.assertEqual(len(tasks),4)
        for target in tasks[2:]:
            for field in ('prompt','seconds','assets'):self.assertEqual(target[field],source[field])
            self.assertTrue(target['held_for_start']);self.assertEqual(target['status'],'PENDING')
        record=tasks[0];self.assertEqual(record['promoted_task_id'],record['promoted_tasks']['768p'])
        # Historical target remains deduplicated, even without the new map.
        record.pop('promoted_tasks');store=lab.read_store();store['tasks'][0]=record;lab.save_store(store)
        self.assertTrue(lab.promote({'task_id':source['task_id']})['already_added'])

    def test_receive_single_output_not_second_pass(self):
        task=self.enqueue()[0];task.update(quality='single768p',server_url='http://invalid',prompt_id='receipt')
        lab.update(task['task_id'],**{k:task[k] for k in ('quality','server_url','prompt_id')})
        job={'receipt':{'status':{'status_str':'success'},'outputs':{
            '16':{'gifs':[{'filename':'single.mp4','type':'output'}]},
            '93':{'gifs':[{'filename':'wrong.mp4','type':'output'}]}}}}
        with patch.object(lab,'remote_json',return_value=job),patch.object(lab,'download_video') as download:
            lab.receive(task)
        self.assertIn('filename=single.mp4',download.call_args.args[0])
        self.assertTrue(lab.read_store()['tasks'][0]['output_file'].startswith('prompt_lab/single768p/'))

    def test_invalid_target_and_failed_save_are_atomic(self):
        source=self.source();before=lab.read_store()
        with self.assertRaises(ValueError):lab.promote({'task_id':source['task_id'],'quality':'bad'})
        with patch.object(lab,'save_store',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):lab.promote({'task_id':source['task_id'],'quality':'single768p'})
        self.assertEqual(lab.read_store(),before)
