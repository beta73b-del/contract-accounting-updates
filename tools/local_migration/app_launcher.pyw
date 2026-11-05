# -*- coding: utf-8 -*-
"""Silent Python launcher: starts the GUI and makes a backup after it closes.

Run via pythonw.exe, not as the PyInstaller app itself. The database is never replaced.
"""
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from backup_service import backup_once


def main():
    folder = Path(__file__).resolve().parent
    config_path = folder / 'backup_config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    app_exe = Path(config['installed_exe'])
    log_file = Path(config['local_backup_dir']).parent / 'backup_service.log'
    # This is an independent OS launch; do not propagate PyInstaller onefile runtime variables.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith('_PYI_') and k.upper() != '_MEIPASS2'}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    try:
        subprocess.run([str(app_exe)], cwd=str(folder), env=env, check=False)
    except Exception as exc:
        with log_file.open('a', encoding='utf-8') as log:
            log.write(f'[{datetime.now().isoformat()}] EXE не запустился: {exc}\n')
        return
    try:
        result = backup_once(Path(config['local_database']), Path(config['attachments']),
                             Path(config['local_backup_dir']), Path(config['cloud_backup_dir']))
        with log_file.open('a', encoding='utf-8') as log:
            log.write(f'[{datetime.now().isoformat()}] После закрытия: {result}\n')
    except Exception:
        with log_file.open('a', encoding='utf-8') as log:
            log.write(f'[{datetime.now().isoformat()}] Ошибка после закрытия:\n{traceback.format_exc()}\n')


if __name__ == '__main__':
    main()