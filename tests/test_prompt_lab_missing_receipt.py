import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
import test_prompt_lab
from server.services import prompt_lab as lab


class MissingReceiptTests(unittest.TestCase):
    setUp = test_prompt_lab.PromptLabTests.setUp
    enqueue = test_prompt_lab.PromptLabTests.enqueue

    def receipt_task(self):
        task = self.enqueue()[0]
        return lab.update(task['task_id'], status='REVIEW', server_url='http://127.0.0.1:8188',
                          prompt_id='original-receipt', client_id='unique-attempt')

    def run_receiver(self, task, remote):
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
            self.assertLess(clock[0], 100, 'receiver did not stop')
        def request(url, payload=None):
            self.assertIsNone(payload, 'receipt recovery must never submit a job')
            return remote(url, clock[0])
        def download(url, target):
            Path(target).write_bytes(b'video')
        with patch.object(lab.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(lab.time, 'sleep', side_effect=sleep), \
             patch.object(lab, 'remote_json', side_effect=request), \
             patch.object(lab, 'download_video', side_effect=download):
            lab.worker(None, task)
        return lab.read_store()['tasks'][0], clock[0]

    def completed(self):
        return {'original-receipt': {'status': {'status_str': 'success'}, 'outputs': {
            '16': {'gifs': [{'filename': 'video.mp4', 'type': 'output'}]}}}}

    def test_confirmed_missing_receipt_preserves_task_and_unlocks_worker(self):
        task = self.receipt_task()
        row, elapsed = self.run_receiver(task, lambda url, now:
                                        {'queue_running': [], 'queue_pending': []} if url.endswith('/queue') else {})
        self.assertGreaterEqual(elapsed, 30)
        self.assertEqual((row['status'], row['error_code']), ('REVIEW', 'LOCAL_RECEIPT_MISSING'))
        self.assertEqual(row['prompt_id'], task['prompt_id'])
        self.assertEqual(row['client_id'], task['client_id'])
        self.assertEqual(lab.read_store()['tasks'][1]['status'], 'PENDING')
        self.assertFalse(lab.busy())

    def test_queued_receipt_or_client_is_not_missing(self):
        for by_client in (False, True):
            with self.subTest(by_client=by_client):
                task = self.receipt_task() if not by_client else lab.read_store()['tasks'][0]
                def remote(url, now):
                    if url.endswith('/queue'):
                        entry = [1, 'other-id' if by_client else task['prompt_id'], {},
                                 {'client_id': task['client_id']} if by_client else {}]
                        return {'queue_running': [entry], 'queue_pending': []}
                    return {} if now < 48 else self.completed()
                row, _ = self.run_receiver(task, remote)
                self.assertEqual(row['status'], 'DONE')
                self.assertEqual(row.get('error_code'), '')

    def test_network_or_malformed_queue_breaks_absence_confirmation(self):
        task = self.receipt_task()
        for failure in ('network', 'malformed'):
            with self.subTest(failure=failure):
                def remote(url, now):
                    if url.endswith('/queue'):
                        if now == 20:
                            if failure == 'network':
                                raise urllib.error.URLError('offline')
                            return {}
                        return {'queue_running': [], 'queue_pending': []}
                    return {} if now < 48 else self.completed()
                row, _ = self.run_receiver(task, remote)
                self.assertEqual(row['status'], 'DONE')

    def test_only_confirmed_missing_review_allows_new_tasks_and_never_retries_old(self):
        task = self.receipt_task()
        with patch.object(lab, 'load_tasks', return_value=[]):
            with self.assertRaises(ValueError):
                lab.start('test')
        lab.update(task['task_id'], error_code='LOCAL_RECEIPT_MISSING')
        seen = []
        def execute(row, srv):
            seen.append(row['task_id'])
            lab.update(row['task_id'], status='DONE')
        with patch.object(lab, 'load_tasks', return_value=[]), patch.object(lab, 'preflight'), \
             patch.object(lab, 'remote_json', return_value={}), \
             patch.object(lab, 'execute', side_effect=execute):
            lab.start('test')
            for thread in lab._threads.values():
                thread.join(5)
                self.assertFalse(thread.is_alive())
        self.assertEqual(seen, ['LAB-000002'])
        self.assertEqual(lab.read_store()['tasks'][0]['status'], 'REVIEW')
        with self.assertRaises(ValueError):
            lab.task_action({'task_id': task['task_id'], 'action': 'retry'})
        lab.task_action({'task_id': task['task_id'], 'action': 'retry', 'confirmed': True})
        self.assertEqual(lab.read_store()['tasks'][0]['error_code'], '')
