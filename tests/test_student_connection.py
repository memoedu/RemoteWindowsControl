"""Check the quick action without accessing an enrolled student computer."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

import check_student_connection as action


class StudentConnectionTests(unittest.TestCase):
    host = 'rwg-0000abcd-abcdef123456'
    token = '00' * 32

    def setUp(self):
        artifacts = Path(r'D:\Artifacts\RemoteWindowsControl\validation')
        artifacts.mkdir(parents=True, exist_ok=True)
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(dir=artifacts)))
        self.exe = self.root / 'tailscale.exe'
        self.fixture = {'BackendState': 'Running', 'CurrentTailnet': {'Name': 'fixture'},
                        'TailscaleIPs': ['100.100.100.90'], 'Self': {'Online': True}, 'Peer': {'peer': {
                            'DNSName': self.host + '.test.invalid.', 'TailscaleIPs': ['100.100.100.91'],
                            'Online': False, 'LastSeen': '2025-12-31T23:55:00Z'}}}
        action.save_json(self.root / 'universal.json', {'tailnetName': 'fixture', 'fleetId': '0000abcd',
                         'teacherIP': '100.100.100.90', 'dnsSuffix': 'test.invalid',
                         'port': 8765, 'mcpSecret': self.token})
        self.request = {'operationId': 'fixture-operation', 'text': '', 'fields': {}}

    def test_offline_receipt_can_be_replayed_and_missing_peer_is_unknown(self):
        with patch.object(action.subprocess, 'check_output', return_value=json.dumps(self.fixture)), \
                patch.object(action, 'probe') as probe:
            receipt = action.run(self.request, self.exe, self.root, self.host)
            self.assertEqual(receipt['state'], 'completed')
            self.assertIn('2026-01-01 07:55:00', receipt['detail'])
            probe.assert_not_called()
        with patch.object(action.subprocess, 'check_output', side_effect=subprocess.TimeoutExpired('test', 1)) as command:
            self.assertEqual(receipt, action.run(self.request, self.exe, self.root, self.host))
            command.assert_not_called()
            self.assertEqual(action.check(self.exe, self.root, self.host)[0], 'unknown')
        self.fixture['Peer'].clear()
        with patch.object(action.subprocess, 'check_output', return_value=json.dumps(self.fixture)):
            self.assertEqual(action.check(self.exe, self.root, self.host)[0], 'unknown')

    def test_online_runs_only_authenticated_initialize(self):
        received = []
        token = self.token

        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path, request['method']))
                if self.headers.get('Authorization') != 'Bearer ' + token:
                    self.send_error(401)
                    return
                if self.server.redirect:
                    self.send_response(302)
                    self.send_header('Location', '/forwarded')
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                result = {'id': request['id'], 'jsonrpc': '2.0', 'result': {'serverInfo': {'name': 'fixture'}}}
                self.wfile.write((': keepalive\n\ndata: ' + json.dumps(result) + '\n\n').encode())

            def do_GET(self):
                received.append((self.path, 'GET'))
                self.send_error(418)

        server = self.enterContext(ThreadingHTTPServer(('127.0.0.1', 0), Endpoint))
        server.redirect = False
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        targets = {self.host: ('127.0.0.1', server.server_port, token)}
        # Only the fixture routes the authenticated check to a loopback server.
        with patch.object(action.subprocess, 'check_output', return_value='{}'), \
                patch.object(action, 'paired_devices', return_value=(
                    [{'hostName': self.host, 'online': True}], targets)):
            self.assertEqual(action.check(self.exe, self.root, self.host)[0], 'completed')
            targets[self.host] = ('127.0.0.1', server.server_port, 'invalid-token')
            self.assertIn('HTTP 401', action.check(self.exe, self.root, self.host)[1])
            targets[self.host] = ('127.0.0.1', server.server_port, token)
            server.redirect = True
            self.assertEqual(action.check(self.exe, self.root, self.host)[0], 'failed')
        self.assertEqual(received, [('/mcp', 'initialize')] * 3)

    def test_rejects_instructions_in_stdin(self):
        with self.assertRaises(ValueError):
            action.run({**self.request, 'fields': {'host': self.host}}, self.exe, self.root, self.host)


if __name__ == '__main__':
    unittest.main()
