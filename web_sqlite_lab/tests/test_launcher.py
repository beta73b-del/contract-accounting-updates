"""Headless source launcher test; equivalent bundled smoke command is in build CI."""
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parent.parent

class LauncherTests(unittest.TestCase):
    def test_headless_launcher_serves_api_without_gui_or_python_workbook(self):
        with TemporaryDirectory(prefix="contracts-launch-test-") as temp:
            db=Path(temp)/"synthetic.sqlite"
            env=os.environ.copy()
            env.update(CONTRACTS_SQLITE_LAB_TEST="1",
                       CONTRACTS_SQLITE_LAB_DB=str(db),
                       PYTHONDONTWRITEBYTECODE="1")
            flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
            try:
                proc=subprocess.run([sys.executable,str(ROOT/"launcher.py"),"--smoke"],
                                    env=env,cwd=ROOT,stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,timeout=20,creationflags=flags)
            except subprocess.TimeoutExpired as exc:
                partial=(exc.stderr or b"")[-1600:].decode("utf-8","replace")
                self.fail("Headless launcher timed out. Diagnostic tail: "+partial)
            self.assertEqual(proc.returncode,0,
                             "Headless launcher failed: "+proc.stderr[-1600:].decode("utf-8","replace"))
            self.assertTrue(db.is_file())
            self.assertFalse((ROOT/"data"/"contracts-real.xlsx").exists())
if __name__=="__main__":unittest.main()
