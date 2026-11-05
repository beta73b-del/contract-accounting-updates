#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative relocation of UchetZakupok EXE/data out of a cloud-sync folder.

NEVER overwrites the existing working DB or the old cloud folder. Requires app closed.
Requires Python 3.11+ for sqlite3 and safe migration checks.
"""
from __future__ import annotations
import argparse
import base64
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
from contextlib import closing
from pathlib import Path

from backup_service import backup_once, db_integrity, sha256, snapshot_db, attachments_list


class MigrationError(RuntimeError):
    pass


def is_app_running() -> bool:
    if os.name != 'nt':
        return False
    try:
        completed = subprocess.run(
            ['tasklist.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, timeout=10, errors='replace'
        )
        if completed.returncode != 0:
            raise MigrationError('Не удалось получить список запущенных процессов.')
        for row in csv.reader(completed.stdout.splitlines()):
            if row and row[0].lower().startswith('uchetzakupok') and row[0].lower().endswith('.exe'):
                return True
        return False
    except (OSError, subprocess.TimeoutExpired):
        # Fail closed: the tool is required to confirm that the EXE is not running.
        raise MigrationError('Не удалось проверить запущенные процессы. Закройте программу и повторите.')


def create_shortcut(target_exe: Path) -> dict:
    """Create a NEW desktop link (without changing any old cloud shortcuts)."""
    if os.name != 'nt':
        return {'created': False, 'reason': 'Windows only'}
    # EncodedCommand prevents quotation/encoding issues in redirected Desktop paths.
    # This is an inline command, not a .ps1 script, and does not relax execution policy.
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    use_launcher = pythonw.is_file() and (target_exe.parent / 'app_launcher.pyw').is_file()
    launch_target = pythonw if use_launcher else target_exe
    target = str(launch_target).replace("'", "''")
    icon_path = str(target_exe).replace("'", "''")
    arguments = ('"' + str(target_exe.parent / 'app_launcher.pyw') + '"') if use_launcher else ''
    arguments = arguments.replace("'", "''")
    script = ("$d=[Environment]::GetFolderPath('Desktop');"
              "$p=Join-Path $d 'Учет заключенных контрактов (локально).lnk';"
              "if(Test-Path -LiteralPath $p){exit 10};"
              "$w=New-Object -ComObject WScript.Shell;"
              "$s=$w.CreateShortcut($p);"
              f"$s.TargetPath='{target}';"
              f"$s.Arguments='{arguments}';"
              f"$s.WorkingDirectory='{str(target_exe.parent).replace(chr(39), chr(39)*2)}';"
              f"$s.IconLocation='{icon_path},0';"
              "$s.Save()")
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    try:
        run = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                             capture_output=True, timeout=20, errors='replace', text=True)
        return {'created': run.returncode == 0,
                'reason': 'shortcut already exists' if run.returncode == 10 else (run.stderr[:500] if run.returncode else '')}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'created': False, 'reason': str(exc)}


def _database_counts(path: Path) -> dict:
    if not path.is_file():
        return {'exists': False}
    try:
        db_integrity(path)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {'exists': True, 'valid': True, **{
                name: db.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                for name in ['purchases', 'purchase_items', 'attachments', 'stock_receipts'] if name in tables}}
    except Exception as exc:
        return {'exists': True, 'valid': False, 'reason': str(exc)}


def _safety_archive(local_db: Path, legacy_db: Path, att_dir: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.partial_' + uuid.uuid4().hex)
    try:
        with tempfile.TemporaryDirectory(prefix='uchet_migration_db_') as temp:
            snap = Path(temp) / 'working.db'
            snapshot_db(local_db, snap)
            legacy_snapshot = None
            if legacy_db.is_file():
                legacy_snapshot = Path(temp) / 'legacy_master.db'
                snapshot_db(legacy_db, legacy_snapshot)
            with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                z.write(snap, 'working.db')
                if legacy_snapshot:
                    z.write(legacy_snapshot, 'legacy_master.db')
                for f in attachments_list(att_dir):
                    z.write(f, 'attachments/' + f.relative_to(att_dir).as_posix())
                z.writestr('migration_manifest.json', json.dumps({
                    'created_at': datetime.now().isoformat(), 'working_sha256': sha256(snap),
                    'working_counts': _database_counts(local_db), 'legacy_counts': _database_counts(legacy_db)
                }, indent=2, ensure_ascii=False))
        with zipfile.ZipFile(partial, 'r') as z:
            if z.testzip() is not None or 'working.db' not in z.namelist():
                raise MigrationError('Не прошла проверка резервного архива перед переносом')
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _writable_space(destination: Path, needed: int) -> None:
    existing = destination
    while not existing.exists():
        if existing.parent == existing:
            break
        existing = existing.parent
    if shutil.disk_usage(existing).free < needed * 2 + 40 * 1024 * 1024:
        raise MigrationError('Недостаточно свободного места для резервирования и переноса.')


def _nonempty_wal(local_db: Path) -> bool:
    return any(p.is_file() and p.stat().st_size for p in [
        Path(str(local_db) + '-wal'), Path(str(local_db) + '-journal')])


def migrate(source_exe: Path, target_dir: Path, local_root: Path,
            cloud_backups: Path, *, dry_run: bool = False,
            install_task: bool = False, shortcut: bool = False) -> dict:
    source_exe = source_exe.expanduser().resolve()
    source_data = source_exe.parent / 'data'
    target_dir = target_dir.expanduser().resolve()
    local_root = local_root.expanduser().resolve()
    cloud_backups = cloud_backups.expanduser().resolve()
    local_db = local_root / 'zakupki_work.db'
    legacy_db = source_data / 'zakupki.db'
    if not source_exe.is_file() or source_exe.stat().st_size < 1024 * 1024:
        raise MigrationError('Не найден пригодный UchetZakupok.exe: ' + str(source_exe))
    if not source_data.is_dir():
        raise MigrationError('Отсутствует исходный каталог data: ' + str(source_data))
    if any(p.is_symlink() for p in source_data.rglob('*')):
        raise MigrationError('В старой папке data есть символьные ссылки. Для безопасности перенос прерван.')
    if not local_db.is_file():
        raise MigrationError('Рабочая база не найдена. Автоматически создавать пустую базу запрещено: ' + str(local_db))
    db_integrity(local_db)
    if legacy_db.is_file():
        db_integrity(legacy_db)
    if _nonempty_wal(local_db):
        raise MigrationError('Рядом с рабочей SQLite есть незавершённый WAL/journal. Закройте приложение штатно и повторите.')
    if target_dir.exists():
        raise MigrationError('Локальная папка назначения уже существует. Ничего не перезаписано: ' + str(target_dir))
    if target_dir == source_exe.parent or source_exe.parent in target_dir.parents:
        raise MigrationError('Новый каталог должен находиться вне исходного облачного каталога.')
    if source_data in cloud_backups.parents or source_exe.parent in cloud_backups.parents:
        # Cloud backups should live outside of the source program's data folder.
        raise MigrationError('Облачный каталог архивов должен находиться вне старой папки программы.')
    counts = {'working': _database_counts(local_db), 'legacy_master': _database_counts(legacy_db)}
    if is_app_running():
        raise MigrationError('UchetZakupok.exe сейчас запущен. Закройте его перед переносом.')
    total = sum(p.stat().st_size for p in source_data.rglob('*') if p.is_file() and not p.is_symlink())
    total += source_exe.stat().st_size + local_db.stat().st_size
    _writable_space(target_dir.parent, total)
    description = {'status': 'dry_run' if dry_run else 'prepared', 'source_exe': str(source_exe),
                   'target_exe': str(target_dir / 'UchetZakupok.exe'), 'working_db': str(local_db),
                   'cloud_backups': str(cloud_backups), 'counts': counts}
    if dry_run:
        return description

    local_root.mkdir(parents=True, exist_ok=True)
    safety_dir = local_root / 'migration_safety'
    safety_name = 'migration_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.zip'
    safety = _safety_archive(local_db, legacy_db, source_data / 'attachments', safety_dir / safety_name)
    description['safety_archive'] = str(safety)

    stage = target_dir.with_name(target_dir.name + '.stage_' + uuid.uuid4().hex)
    try:
        stage.mkdir(parents=True)
        shutil.copy2(source_exe, stage / 'UchetZakupok.exe')
        shutil.copytree(source_data, stage / 'data', symlinks=False)
        # The new local mirror MUST match the authoritative local working file byte for byte.
        # Old cloud master was preserved separately in the safety archive.
        shutil.copy2(local_db, stage / 'data' / 'zakupki.db')
        local_sha = sha256(local_db)
        if sha256(stage / 'data' / 'zakupki.db') != local_sha:
            raise MigrationError('Хэш локальной мастер-копии не совпал с рабочей базой.')
        state = {'protocol': 1, 'db_sha256': local_sha, 'synced_at': datetime.now().isoformat(),
                 'computer': os.environ.get('COMPUTERNAME', 'local')}
        (stage / 'data' / 'sync_state.json').write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        (stage / 'data' / 'app.lock').unlink(missing_ok=True)
        shutil.copy2(Path(__file__).parent / 'backup_service.py', stage / 'backup_service.py')
        shutil.copy2(Path(__file__), stage / 'local_storage_setup.py')
        shutil.copy2(Path(__file__).parent / 'app_launcher.pyw', stage / 'app_launcher.pyw')
        config = {'schema': 1, 'local_database': str(local_db),
                  'attachments': str(target_dir / 'data' / 'attachments'),
                  'local_backup_dir': str(local_root / 'backups'),
                  'cloud_backup_dir': str(cloud_backups),
                  'installed_exe': str(target_dir / 'UchetZakupok.exe')}
        config_path = target_dir / 'backup_config.json'
        (stage / 'backup_config.json').write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        # A convenience launcher, and a task-friendly entry point for archive backups.
        py = sys.executable
        backup_bat = target_dir / 'Run_Backup.cmd'
        (stage / 'Run_Backup.cmd').write_text(
            f'@echo off\n"{py}" "{target_dir / "backup_service.py"}" --config "{config_path}" '
            f'>> "{local_root / "backup_service.log"}" 2>&1\nexit /b %errorlevel%\n', encoding='utf-8')
        db_integrity(stage / 'data' / 'zakupki.db')
        if sha256(stage / 'UchetZakupok.exe') != sha256(source_exe):
            raise MigrationError('Хэш EXE после переноса не совпал.')
        # Stage and target share a parent, so rename is atomic on this volume.
        os.replace(stage, target_dir)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    description['config_path'] = str(config_path)
    first = backup_once(local_db, target_dir / 'data' / 'attachments',
                        local_root / 'backups', cloud_backups, force=True)
    description['backup'] = first
    description['status'] = 'installed' if first.get('cloud_status') == 'ok' else 'installed_local_cloud_pending'
    if shortcut:
        description['shortcut'] = create_shortcut(target_dir / 'UchetZakupok.exe')
    if install_task and os.name == 'nt':
        try:
            task = subprocess.run(['schtasks.exe', '/Create', '/SC', 'HOURLY', '/MO', '4',
                                   '/TN', 'UchetZakupok_Backup', '/TR', str(backup_bat)],
                                  capture_output=True, text=True, timeout=15, errors='replace')
            if task.returncode != 0:
                description['task_created'] = False
                description['task_message'] = (task.stdout + ' ' + task.stderr).strip()[:500]
            else:
                # Allow the backup task to run on battery and after a missed trigger.
                ps = ("Set-ScheduledTask -TaskName 'UchetZakupok_Backup' "
                      "-Action (New-ScheduledTaskAction "
                      "-Execute (Join-Path $env:SystemRoot 'System32\\cmd.exe') "
                      "-Argument '/D /C \"" + str(backup_bat).replace("'", "''") + "\"' "
                      "-WorkingDirectory '" + str(target_dir).replace("'", "''") + "') "
                      "-Settings (New-ScheduledTaskSettingsSet "
                      "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                      "-StartWhenAvailable) | Out-Null")
                encoded = base64.b64encode(ps.encode('utf-16-le')).decode('ascii')
                tuned = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                       capture_output=True, text=True, timeout=20, errors='replace')
                description['task_created'] = tuned.returncode == 0
                description['task_message'] = (task.stdout + ' ' + task.stderr + ' ' +
                                               tuned.stderr).strip()[:500]
        except (OSError, subprocess.TimeoutExpired) as ex:
            description['task_created'] = False
            description['task_message'] = str(ex)
    return description


def main(argv=None) -> int:
    if sys.version_info < (3, 11):
        print('Требуется Python 3.11 или новее.', file=sys.stderr)
        return 2
    p = argparse.ArgumentParser(description='Безопасный переход Учет заключенных контрактов с облака на диск C:')
    p.add_argument('--source-exe', required=True)
    p.add_argument('--cloud-backups', required=True)
    p.add_argument('--local-root', default=str(Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'UchetZakupok'))
    p.add_argument('--install-dir', default=str(Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'UchetZakupok'))
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--install-task', action='store_true')
    p.add_argument('--shortcut', action='store_true')
    args = p.parse_args(argv)
    try:
        result = migrate(Path(args.source_exe), Path(args.install_dir), Path(args.local_root),
                         Path(args.cloud_backups), dry_run=args.dry_run,
                         install_task=args.install_task, shortcut=args.shortcut)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result['status'] in ('dry_run', 'installed') else 3
    except Exception as ex:
        print('ОШИБКА. Рабочая база и исходная папка не изменены: ' + str(ex), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())