#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verified local SQLite + attachments backup and archive-only cloud mirroring.

No running database file is copied directly: sqlite3.Connection.backup() is used.
Only archives created by this service are subject to retention.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timedelta
from contextlib import closing
from pathlib import Path

PREFIX = 'zakupki_safe_'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def db_integrity(path: Path) -> None:
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError('База отсутствует или пуста: ' + str(path))
    # ro prevents accidental creation/replacement of a database.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        result = db.execute('PRAGMA integrity_check').fetchone()
    if not result or result[0] != 'ok':
        raise RuntimeError('Ошибка целостности SQLite: ' + str(result))


def snapshot_db(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    source = source.resolve()
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=20)) as src:
        with closing(sqlite3.connect(destination, timeout=20)) as dst:
            src.backup(dst, pages=256, sleep=0.05)
    db_integrity(destination)


def attachments_list(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if not root.is_dir():
        raise RuntimeError('Некорректный путь к вложениям: ' + str(root))
    result = []
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for name in files:
            f = Path(base) / name
            if f.is_symlink():
                raise RuntimeError('Символическая ссылка во вложениях: ' + str(f))
            result.append(f)
    return sorted(result)


def fingerprint(snapshot: Path, attachments: Path, paths: list[Path]) -> str:
    """Content fingerprint of the consistent SQLite snapshot and attachments."""
    h = hashlib.sha256()
    h.update(sha256(snapshot).encode('ascii'))
    for f in paths:
        rel = f.relative_to(attachments).as_posix()
        h.update(rel.encode('utf-8'))
        h.update(sha256(f).encode('ascii'))
    return h.hexdigest()


def _atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + '.partial_' + uuid.uuid4().hex)
    try:
        with tmp.open('w', encoding='utf-8') as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _copy_archive_verified(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = dest_dir / src.name
    src_hash = sha256(src)
    if dst.is_file():
        if sha256(dst) == src_hash:
            return dst
        # Keep another device's archive unchanged if the name collides.
        raise RuntimeError('В облаке уже есть архив с этим именем и другой SHA-256: ' + dst.name)
    temp = dest_dir / (src.name + '.partial_' + uuid.uuid4().hex)
    try:
        with src.open('rb') as inp, temp.open('wb') as out:
            shutil.copyfileobj(inp, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if sha256(temp) != src_hash:
            raise RuntimeError('SHA-256 копии архива не совпадает с оригиналом')
        with zipfile.ZipFile(temp) as z:
            if z.testzip() is not None:
                raise RuntimeError('Архив в облачном каталоге повреждён')
        os.replace(temp, dst)
        if sha256(dst) != src_hash:
            raise RuntimeError('Нарушена целостность после записи архива')
        return dst
    finally:
        temp.unlink(missing_ok=True)


def _retained(files: list[Path], now: datetime) -> set[Path]:
    """Keep five most recent + 30 daily + 12 weekly + 12 monthly points."""
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    keep: set[Path] = set(files[:5])
    daily: set[str] = set()
    weekly: set[str] = set()
    monthly: set[str] = set()
    for f in files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        delta = now - mtime
        if timedelta(0) <= delta <= timedelta(days=30):
            key = mtime.strftime('%Y-%m-%d')
            if key not in daily:
                daily.add(key)
                keep.add(f)
        if timedelta(0) <= delta <= timedelta(weeks=12):
            y, w, _ = mtime.isocalendar()
            key = f'{y}-{w}'
            if key not in weekly:
                weekly.add(key)
                keep.add(f)
        if timedelta(0) <= delta <= timedelta(days=366):
            key = mtime.strftime('%Y-%m')
            if key not in monthly:
                monthly.add(key)
                keep.add(f)
    return keep


def cleanup(directory: Path, now: datetime | None = None) -> int:
    if not directory.is_dir():
        return 0
    now = now or datetime.now()
    files = list(directory.glob(PREFIX + '*.zip'))
    keep = _retained(files, now)
    deleted = 0
    for f in files:
        if f not in keep:
            f.unlink()
            deleted += 1
    return deleted


def backup_once(local_db: Path, attachments: Path, archives: Path,
                cloud_archives: Path | None, force: bool = False) -> dict:
    """Create an archive only if data changed, then mirror latest to cloud.

    If cloud is offline, local backup stays intact and a later run retries it.
    """
    archives.mkdir(parents=True, exist_ok=True)
    state_path = archives / 'backup_service_state.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    with tempfile.TemporaryDirectory(prefix='uchet_snapshot_', dir=archives) as temp_dir:
        snapshot = Path(temp_dir) / 'zakupki.db'
        snapshot_db(local_db, snapshot)
        files = attachments_list(attachments)
        new_fp = fingerprint(snapshot, attachments, files)
        existing = sorted(archives.glob(PREFIX + '*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
        unchanged = bool(existing and new_fp == state.get('fingerprint'))
        if unchanged and not force:
            result = {'created': False, 'local_archive': str(existing[0]), 'fingerprint': new_fp}
            archive = existing[0]
        else:
            now = datetime.now()
            name = PREFIX + now.strftime('%Y%m%d_%H%M%S_%f') + '.zip'
            archive = archives / name
            partial = archives / (name + '.partial_' + uuid.uuid4().hex)
            try:
                with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    z.write(snapshot, 'zakupki.db')
                    for item in files:
                        z.write(item, 'attachments/' + item.relative_to(attachments).as_posix())
                    z.writestr('backup_manifest.json', json.dumps({
                        'format': 1, 'created_at': now.isoformat(), 'db_sha256': sha256(snapshot),
                        'attachment_count': len(files), 'data_fingerprint': new_fp
                    }, ensure_ascii=False))
                with zipfile.ZipFile(partial) as z:
                    if z.testzip() is not None or 'zakupki.db' not in z.namelist():
                        raise RuntimeError('Проверка сформированного ZIP не пройдена')
                os.replace(partial, archive)
                _atomic_json(state_path, {'fingerprint': new_fp, 'last_archive': archive.name,
                                          'updated_at': now.isoformat()})
            finally:
                partial.unlink(missing_ok=True)
            result = {'created': True, 'local_archive': str(archive), 'fingerprint': new_fp}
    cleanup(archives)
    # Retry ALL retained local archives after offline periods (not only the newest).
    # Never treat cloud availability as a prerequisite for saving local data.
    if cloud_archives is not None:
        try:
            mirrored = None
            count = 0
            for item in sorted(archives.glob(PREFIX + '*.zip')):
                copied = _copy_archive_verified(item, cloud_archives)
                if item == archive:
                    mirrored = copied
                count += 1
            result['cloud_archive'] = str(mirrored or (cloud_archives / archive.name))
            result['cloud_status'] = 'ok'
            result['cloud_archives_checked'] = count
            cleanup(cloud_archives)
        except (OSError, RuntimeError, zipfile.BadZipFile) as e:
            result['cloud_status'] = 'failed'
            result['cloud_error'] = str(e)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Резервирование SQLite и вложений Учет контрактов')
    p.add_argument('--config', required=True, help='JSON-конфигурация после локальной установки')
    p.add_argument('--force', action='store_true', help='Создать архив даже без изменений')
    args = p.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    try:
        result = backup_once(Path(config['local_database']), Path(config['attachments']),
                             Path(config['local_backup_dir']), Path(config['cloud_backup_dir']),
                             force=args.force)
    except Exception as exc:
        print('ОШИБКА резервирования:', str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('cloud_status') != 'failed' else 3


if __name__ == '__main__':
    raise SystemExit(main())