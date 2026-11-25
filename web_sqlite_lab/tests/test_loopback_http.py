"""Guard against macOS blocking reverse DNS during loopback listener creation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent

class LoopbackTests(unittest.TestCase):
    def test_listener_startup_never_calls_getfqdn(self):
        code="""
from unittest.mock import patch
from server import Handler,HOST,LoopbackHTTPServer,store
with patch('socket.getfqdn',side_effect=AssertionError('DNS lookup forbidden')):
    server=LoopbackHTTPServer((HOST,0),Handler)
try:
    assert server.server_port>0
    assert server.server_name==HOST
finally:
    server.server_close()
    store.close()
"""
        with tempfile.TemporaryDirectory(prefix="qa-loopback-") as temp:
            env=os.environ.copy()
            env.update(CONTRACTS_SQLITE_LAB_TEST="1",
                       CONTRACTS_SQLITE_LAB_DB=str(Path(temp)/"fake.sqlite"),
                       PYTHONPATH=str(ROOT))
            proc=subprocess.run([sys.executable,"-c",code],cwd=str(ROOT),env=env,
                                capture_output=True,text=True,timeout=10)
            self.assertEqual(proc.returncode,0,
                             "Local HTTP listener triggered reverse DNS or failed: "+proc.stderr[-1200:])
if __name__=="__main__":
    unittest.main()
