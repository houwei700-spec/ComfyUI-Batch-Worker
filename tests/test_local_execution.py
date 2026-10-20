import copy
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import test_bugfixes
import test_prompt_lab
from server.services import task_service as tasks, prompt_lab as lab
from server.services.endpoint_service import normalize_endpoint, apply_model_overrides, find_submission_receipt
from server.services import endpoint_service
from server.workers import task_runner as runner, batch_dispatcher as dispatch
from server.routes import api_router as api


class LocalExecutionTests(unittest.TestCase):
    setUp = test_bugfixes.BugfixTests.setUp
    task = test_bugfixes.BugfixTests.task

    def workflow(self):
        wf = {'18': {'class_type': 'LoadImage', 'inputs': {'image': ''}},
              '35': {'class_type': 'LoadImage', 'inputs': {'image': ''}},
              '16': {'class_type': 'VHS_VideoCombine', 'inputs': {'save_output': True}}}
        (self.root / 'tpl.json').write_text(json.dumps(wf), encoding='utf-8')
        tasks.save_tasks([self.task(status='QUEUED')])

    def test_aliases_and_wrong_workbench_port(self):
        self.assertEqual(normalize_endpoint('localhost:8188/'), 'http://127.0.0.1:8188')
        for url in ('', 'ftp://localhost:8188', 'http://127.0.0.1:8999', 'http://a:b@localhost:8188'):
            with self.assertRaises(ValueError): normalize_endpoint(url)

    def test_submission_timeout_is_review_and_survives_restart(self):
        self.workflow()
        with patch.object(runner, 'TEMPLATES_DIR', str(self.root)), \
             patch.object(runner, 'resolve_cloud_image_path', side_effect=lambda s,c,n:c+'/'+n), \
             patch.object(runner.urllib.request, 'urlopen', side_effect=TimeoutError('lost response')) as network:
            self.assertFalse(runner.execute_task_on_server({'url':'http://fake','name':'test'}, self.task()))
        self.assertEqual(network.call_count, 1)
        record = tasks.load_tasks()[0]
        self.assertEqual(record['status'], 'REVIEW')
        self.assertEqual(record['server_url'], 'http://fake')
        self.assertTrue(record['client_id'])
        api.handle_post(test_bugfixes.Handler(), '/api/reset_failed', {})
        tasks.recover_interrupted_tasks()
        self.assertEqual(tasks.load_tasks()[0]['status'], 'REVIEW')

    def test_interrupted_receipt_is_preserved_and_can_receive_without_post(self):
        record=self.task(status='RUNNING',prompt_id='original-id',server_url='http://fake',final_node='16')
        tasks.save_tasks([record]); tasks.recover_interrupted_tasks()
        recovered=tasks.load_tasks()[0]
        self.assertEqual(recovered['status'],'REVIEW')
        self.assertEqual(recovered['prompt_id'],'original-id')
        history={'original-id':{'status':{'status_str':'success'},'outputs':{
            '16':{'gifs':[{'filename':'finished.mp4','type':'output'}]}}}}
        with patch.object(runner.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(history).encode())) as network, \
             patch.object(runner,'OUTPUT_DIR',str(self.root/'output')), patch.object(runner,'download_video'):
            self.assertTrue(runner.receive_task(recovered))
        self.assertIn('/history/original-id',network.call_args.args[0])
        self.assertEqual(network.call_count,1)
        self.assertEqual(tasks.load_tasks()[0]['status'],'DONE')

    def test_local_template_uploads_selected_images_before_submission(self):
        self.workflow(); sent=[]
        def network(request, **kwargs):
            sent.append(json.loads(request.data))
            return io.BytesIO(b'{"prompt_id":"local-job"}')
        with patch.object(runner,'TEMPLATES_DIR',str(self.root)), \
             patch.object(runner,'validate_local_workflow'), \
             patch.object(lab,'upload_remote',side_effect=['prompt_lab/model.png','prompt_lab/apparel.png']) as upload, \
             patch.object(runner.urllib.request,'urlopen',side_effect=network), \
             patch.object(runner,'receive_task',return_value=True):
            self.assertTrue(runner.execute_task_on_server({'url':'http://127.0.0.1:8188','name':'local'},self.task()))
        self.assertEqual(upload.call_count,2)
        self.assertEqual(sent[0]['prompt']['18']['inputs']['image'],'prompt_lab/model.png')
        self.assertEqual(sent[0]['prompt']['35']['inputs']['image'],'prompt_lab/apparel.png')
        self.assertEqual(tasks.load_tasks()[0]['prompt_id'],'local-job')

    def test_review_cannot_be_deleted_or_requeued_without_confirmation(self):
        tasks.save_tasks([self.task(status='REVIEW',prompt_id='keep')])
        handler=test_bugfixes.Handler({'task_id':'001'})
        api.handle_post(handler,'/api/task/regenerate',{})
        self.assertFalse(handler.result()['success'])
        api.handle_post(test_bugfixes.Handler(),'/api/delete_task',{'id':['001']})
        self.assertEqual(tasks.load_tasks()[0]['prompt_id'],'keep')

    def test_selected_local_port_excludes_cloud_claims(self):
        tasks.save_tasks([self.task(status='PENDING')])
        with patch.object(dispatch,'cluster_paused',False), patch.object(dispatch,'_selected_server_id','local'):
            self.assertIsNone(dispatch.claim_next_task('cloud','cloud'))
            self.assertIsNotNone(dispatch.claim_next_task('local','local'))

    def test_model_mapping_is_scoped_to_endpoint(self):
        original={'6':{'class_type':'MiniMaxH3EasyLoader','inputs':{'ref2va_model':'cloud-model'}}}
        local=apply_model_overrides({'model_overrides':{'MiniMaxH3EasyLoader':{'ref2va_model':'local-model'}}},copy.deepcopy(original))
        self.assertEqual(local['6']['inputs']['ref2va_model'],'local-model')
        self.assertEqual(apply_model_overrides({},copy.deepcopy(original)),original)

    def test_lost_response_recovers_only_matching_attempt_from_queue(self):
        queue={'queue_running':[[0,'other',{}, {'client_id':'old-attempt'}],
                                [1,'correct',{}, {'client_id':'new-attempt'}]]}
        with patch.object(endpoint_service.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(queue).encode())) as network:
            self.assertEqual(find_submission_receipt('http://local','new-attempt'),'correct')
        self.assertEqual(network.call_count,1)
        self.assertTrue(network.call_args.args[0].endswith('/queue'))

    def test_unknown_receipt_stays_review_without_generation(self):
        task=self.task(status='REVIEW',server_url='http://local',client_id='unconfirmed')
        tasks.save_tasks([task])
        with patch.object(runner,'find_submission_receipt',return_value=None), \
             patch.object(runner.urllib.request,'urlopen') as network:
            self.assertFalse(runner.receive_task(task))
        network.assert_not_called()
        self.assertEqual(tasks.load_tasks()[0]['status'],'REVIEW')


class LocalPromptLabTests(unittest.TestCase):
    setUp = test_prompt_lab.PromptLabTests.setUp
    enqueue = test_prompt_lab.PromptLabTests.enqueue

    def test_busy_local_comfy_waits_without_claiming_and_can_pause(self):
        self.enqueue();lab._enabled=True
        srv={'id':'test','name':'local','url':'http://127.0.0.1:8188'}
        with patch.object(lab.config,'load_server_config',return_value={'servers':[srv]}), \
             patch.object(lab,'remote_json',return_value={'queue_running':[['external-job']]}), \
             patch.object(lab.time,'sleep',side_effect=lambda seconds:lab.pause()), \
             patch.object(lab,'execute') as execute:
            lab.worker(srv,quality='480p')
        execute.assert_not_called()
        self.assertTrue(all(t['status']=='PENDING' for t in lab.read_store()['tasks']))
