"""Cross-platform user-data paths: never place a writable database in an .app/EXE bundle."""
from __future__ import annotations
from pathlib import Path
import os
import sys

APP_DIR_NAME="ContractsIPSqliteLab"

def lab_data_dir(*,frozen=False,source_root=None,platform=None,environment=None,home=None):
    current=sys.platform if platform is None else str(platform)
    env=os.environ if environment is None else environment
    user_home=Path.home() if home is None else Path(home)
    if not frozen:
        if source_root is None:raise ValueError("Development source root is required")
        return Path(source_root).resolve()/"data"
    if current=="win32":
        folder=env.get("LOCALAPPDATA")
        root=Path(folder) if folder else user_home/"AppData"/"Local"
    elif current=="darwin":
        root=user_home/"Library"/"Application Support"
    else:
        folder=env.get("XDG_DATA_HOME")
        root=Path(folder) if folder else user_home/".local"/"share"
    return root/APP_DIR_NAME
