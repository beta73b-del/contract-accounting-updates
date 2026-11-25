"""R6 independent platform path tests (no user directories are touched)."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from platform_paths import APP_DIR_NAME,lab_data_dir

class PlatformPathTests(unittest.TestCase):
    def test_development_does_not_touch_appdata(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)/"source"
            expect=root/"data"
            self.assertEqual(lab_data_dir(frozen=False,source_root=root,
                                          platform="darwin",home=Path(temp)),expect)
            self.assertFalse(expect.exists())
    def test_macos_application_support(self):
        x=lab_data_dir(frozen=True,platform="darwin",home=Path("/Users/qa"),
                       environment={})
        self.assertEqual(x,Path("/Users/qa/Library/Application Support")/APP_DIR_NAME)
    def test_win_local_app_data(self):
        x=lab_data_dir(frozen=True,platform="win32",home=Path("C:/Users/Test"),
                       environment={"LOCALAPPDATA":"D:/Company/TestLocal"})
        self.assertEqual(x,Path("D:/Company/TestLocal")/APP_DIR_NAME)
    def test_win_fallback_without_environment(self):
        x=lab_data_dir(frozen=True,platform="win32",home=Path("/qa"),environment={})
        self.assertEqual(x,Path("/qa/AppData/Local")/APP_DIR_NAME)
    def test_linux_xdg(self):
        x=lab_data_dir(frozen=True,platform="linux",home=Path("/home/qa"),
                       environment={"XDG_DATA_HOME":"/opt/qa"})
        self.assertEqual(x,Path("/opt/qa")/APP_DIR_NAME)
    def test_missing_source_root_rejected(self):
        with self.assertRaises(ValueError):
            lab_data_dir(frozen=False,source_root=None,platform="darwin")
if __name__=="__main__":unittest.main()
