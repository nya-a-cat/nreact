import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from nreact.control import RunControl
from nreact.runs import RunHistory
from nreact.web import make_server


IDENTITY = 'a' * 32


def record(count=10, length=100):
    events = [{"kind": "observation", "step": index + 1, "tool": "echo",
               "text": "x" * length, "index": index, "elapsed_seconds": index * .1}
              for index in range(count)]
    return {"id": IDENTITY, "task": "Task", "config": {}, "model": "fixture",
            "demo": False, "started_at": "2026-09-09T00:00:00+00:00", "status": "finished",
            "steps": count, "events": events, "elapsed_seconds": count * .1, "error": None,
            "result": {"status": "finished", "answer": "ok", "steps": count,
                       "model_calls": count, "usage": {}, "elapsed_seconds": count * .1,
                       "events": [{key: event[key] for key in ('kind', 'step', 'tool', 'text')}
                                  for event in events], "error": None, "reward": None}}


class RunUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.history = RunHistory(Path(self.temporary.name))
        self.history.records[IDENTITY] = record()

    def test_cursor_append_and_result_have_no_duplicate_event_payload(self):
        batch = self.history.updates(IDENTITY, 7)
        self.assertEqual([event['index'] for event in batch['events']], [7, 8, 9])
        self.assertEqual((batch['offset'], batch['next_offset'], batch['event_count']), (7, 10, 10))
        self.assertFalse(batch['has_more'])
        self.assertEqual(batch['result']['answer'], 'ok')
        self.assertNotIn('events', batch['result'])
        self.assertNotIn('config', batch)
        batch['events'][0]['text'] = 'edited'
        self.assertNotEqual(self.history.records[IDENTITY]['events'][7]['text'], 'edited')

    def test_event_batches_reconstruct_the_complete_history(self):
        self.history.records[IDENTITY] = record(300)
        offset, combined, batches = 0, [], []
        while True:
            batch = self.history.updates(IDENTITY, offset)
            batches.append(batch)
            combined.extend(batch['events'])
            offset = batch['next_offset']
            if not batch['has_more']:
                break
            self.assertIsNone(batch['result'])
        self.assertEqual([len(batch['events']) for batch in batches], [128, 128, 44])
        self.assertEqual(combined, self.history.records[IDENTITY]['events'])
        self.assertEqual(batches[-1]['result']['answer'], 'ok')

    def test_text_budget_and_single_large_event_make_progress(self):
        self.history.records[IDENTITY] = record(4, 65000)
        self.assertEqual(self.history.updates(IDENTITY)['next_offset'], 1)
        self.history.records[IDENTITY] = record(2, 200000)
        self.assertEqual(self.history.updates(IDENTITY)['next_offset'], 1)

    def test_idle_updates_are_small_even_with_large_trace(self):
        self.history.records[IDENTITY] = record(100, 10000)
        full = len(json.dumps(self.history.snapshot(IDENTITY)).encode())
        delta = len(json.dumps(self.history.updates(IDENTITY, 100)).encode())
        self.assertGreater(full, 2_000_000)
        self.assertLess(delta, 1000)
        self.assertEqual(self.history.updates(IDENTITY, 100)['events'], [])

    def test_invalid_offsets_and_ids_are_rejected(self):
        for offset in (-1, 11, True, 1.5, '0', None):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.history.updates(IDENTITY, offset)
        with self.assertRaises(ValueError):
            self.history.updates('../outside', 0)

    def test_live_status_is_taken_from_controller_and_empty_queue_has_no_result(self):
        self.history.active, self.history.control = IDENTITY, RunControl(paused=True)
        self.assertEqual(self.history.updates(IDENTITY)['status'], 'paused')
        self.history.active = self.history.control = None
        self.history.records[IDENTITY].update(events=[], result=None, status='queued', steps=0)
        batch = self.history.updates(IDENTITY)
        self.assertEqual(batch['status'], 'queued')
        self.assertEqual(batch['events'], [])
        self.assertIsNone(batch['result'])

    def test_state_does_not_scan_or_read_any_record(self):
        with patch.object(self.history, '_read', side_effect=AssertionError('disk read')):
            self.assertEqual(self.history.state(), {'active': None, 'queued': [], 'queue_paused': False, 'revision': 0})


class RunUpdateHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server = make_server(Path(self.temporary.name) / 'nreact.toml', port=0)
        self.server.app.runs.records[IDENTITY] = record()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temporary.cleanup()

    def request(self, path, payload=None, *, token=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Nreact-Token'] = self.server.app.token
        connection.request('GET' if payload is None else 'POST', path,
                           None if payload is None else json.dumps(payload), headers)
        response = connection.getresponse()
        status, data = response.status, json.loads(response.read())
        connection.close()
        return status, data

    def test_light_state_and_delta_endpoints(self):
        self.assertEqual(self.request('/api/runs/state')[0], 200)
        status, batch = self.request(f'/api/run/updates?id={IDENTITY}&offset=9')
        self.assertEqual(status, 200)
        self.assertEqual(batch['next_offset'], 10)
        self.assertEqual(len(batch['events']), 1)

    def test_invalid_and_out_of_range_http_offsets(self):
        for offset in ('-1', '1.5', 'true', '11', '999999999', '%EF%BC%90'):
            with self.subTest(offset=offset):
                self.assertEqual(self.request(f'/api/run/updates?id={IDENTITY}&offset={offset}')[0], 400)

    def test_queue_controls_require_token_and_validate_action(self):
        for path, payload in (('/api/runs/state', None), (f'/api/run/updates?id={IDENTITY}', None),
                              ('/api/queue/control', {'action': 'pause'})):
            self.assertEqual(self.request(path, payload, token=False)[0], 403)
        self.assertEqual(self.request('/api/queue/control', {'action': 'delete'})[0], 400)
        status, data = self.request('/api/queue/control', {'action': 'pause'})
        self.assertEqual(status, 200)
        self.assertTrue(data['queue_paused'])


if __name__ == '__main__':
    unittest.main()
