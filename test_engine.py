import os
os.environ['ALLOW_PRIVATE_TARGETS'] = '1'
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from demo_server import Demo
from engine import Runner, endpoint_path, origin_url, resolve_public_ip


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Demo)
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def run_case(self, paths='/health', **kw):
        r = Runner()
        c = dict(base=self.base, paths=paths, authorized=True, rps=10, workers=2, duration=1, timeout=1, error_limit=20, token='secret-example')
        c.update(kw)
        r.start(c)
        end = time.monotonic() + 8
        while r.running and time.monotonic() < end:
            time.sleep(.02)
        self.assertFalse(r.running)
        self.assertNotIn('token', r.snapshot()['config'])
        return r.snapshot(True)

    def test_simple_url_exact_count(self):
        for count in (1, 7):
            r = Runner()
            r.start({'url': self.base + '/api/items?page=1', 'count': count})
            end = time.monotonic() + 5
            while r.running and time.monotonic() < end:
                time.sleep(.02)
            self.assertFalse(r.running)
            self.assertEqual(r.sent, count)
            self.assertEqual(len(r.rows), count)
            self.assertEqual(r.config['paths'], ['/api/items?page=1'])
        for count in (0, 1001, 1.5, True):
            with self.assertRaises(ValueError):
                Runner().start({'url': self.base, 'count': count})

    def test_pacing_and_metrics(self):
        s = self.run_case('/health\n/api/items')
        self.assertGreaterEqual(s['sent'], 5)
        self.assertLessEqual(s['sent'], 10)
        self.assertEqual(s['summary']['errors'], 0)
        self.assertEqual(len(s['endpoints']), 2)
        starts = sorted(r['second'] for r in s['requests'])
        self.assertTrue(all(b-a >= .08 for a,b in zip(starts,starts[1:])))

    def test_auto_stop(self):
        for path, status in [('/busy', '503'), ('/limited', '429')]:
            s = self.run_case(path, duration=4)
            self.assertIn(status, s['reason'])
            self.assertLessEqual(s['sent'], 2)

    def test_rolling_error_threshold(self):
        s = self.run_case('/error', rps=50, duration=3)
        self.assertIn('20 request', s['reason'])
        self.assertGreaterEqual(s['sent'], 20)
        self.assertLess(s['sent'], 25)

    def test_concurrency_does_not_queue_backlog(self):
        s = self.run_case('/slow', workers=1, rps=50)
        self.assertGreater(s['skipped'], 0)
        self.assertLessEqual(s['sent'], 4)

    def test_stop(self):
        r = Runner()
        r.start(dict(base=self.base, paths='/health', authorized=True, rps=5, workers=1, duration=5, timeout=1, error_limit=20))
        time.sleep(.1)
        r.stop()
        time.sleep(.3)
        self.assertFalse(r.running)
        self.assertLessEqual(r.sent, 1)

    def test_reject_off_origin_and_invalid_limits(self):
        for path in ['//elsewhere.test/x', 'https://elsewhere.test/x', '/a\\b', '/users/{id}', '/x\r\nHost:x']:
            with self.assertRaises(ValueError):
                endpoint_path(path)
        for base in ['file:///etc/passwd', 'http://user:pass@localhost', self.base+'/api']:
            with self.assertRaises(ValueError):
                origin_url(base)
        with self.assertRaises(ValueError):
            self.run_case(rps=101)
        with self.assertRaises(ValueError):
            self.run_case(authorized=False)

    def test_reject_private_targets_when_online(self):
        os.environ['ALLOW_PRIVATE_TARGETS'] = '0'
        try:
            for host in ('127.0.0.1', 'localhost', '10.0.0.1', '192.168.1.1', '169.254.169.254'):
                with self.assertRaises(ValueError):
                    resolve_public_ip(host, 80)
            with self.assertRaises(ValueError):
                origin_url('http://127.0.0.1')
        finally:
            os.environ['ALLOW_PRIVATE_TARGETS'] = '1'


if __name__ == '__main__':
    unittest.main()
