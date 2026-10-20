import unittest
from server.main import ThreadingHTTPServer, ModularHubHandler


class ExclusiveListenerTests(unittest.TestCase):
    def test_second_server_cannot_share_listening_port(self):
        with ThreadingHTTPServer(('127.0.0.1', 0), ModularHubHandler) as first:
            with self.assertRaises(OSError):
                with ThreadingHTTPServer(first.server_address, ModularHubHandler):
                    self.fail('second service shared the same TCP port')
