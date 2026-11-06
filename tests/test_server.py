import http.client
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        with socket.socket() as s:s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1]
        cls.proc=subprocess.Popen([sys.executable,'-m','liusha.server','--demo','--data-dir',cls.tmp.name,'--port',str(cls.port)],env={**os.environ,'LIUSHA_DASHBOARD_TOKEN':'integration-test-only'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                conn=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=1);conn.request('GET','/');conn.getresponse().read();conn.close();break
            except OSError:time.sleep(.05)
        else:raise RuntimeError('demo did not start')

    @classmethod
    def tearDownClass(cls):cls.proc.terminate();cls.proc.wait(timeout=5);cls.tmp.cleanup()

    def req(self,method,path,auth=True):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        c.request(method,path,headers={'Authorization':'Bearer integration-test-only'} if auth else {})
        r=c.getresponse();result=(r.status,r.read());c.close();return result

    def test_auth(self):self.assertEqual(self.req('GET','/api/usage',False)[0],401)
    def test_no_mutation_endpoint(self):self.assertEqual(self.req('POST','/api/usage')[0],405)
    def test_bad_offset(self):self.assertEqual(self.req('GET','/api/usage?offset=oops')[0],400)
    def test_refresh_only_reads(self):
        p=Path(self.tmp.name)/'usage.jsonl';before=p.read_bytes()
        self.assertEqual(self.req('GET','/api/usage')[0],200);self.assertEqual(p.read_bytes(),before)
    def test_runtime_does_not_return_session_or_prompt(self):
        status,body=self.req('GET','/api/runtime');self.assertEqual(status,200)
        self.assertNotIn(b'session_id',body);self.assertNotIn(b'token',body)
    def test_unknown_file(self):self.assertEqual(self.req('GET','/liusha/server.py')[0],404)
