"""Offline recovery from a mounted backup disk. Never restore from a changing ZIP."""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import re
import shutil
import uuid

from transfer_package import MAX_BYTES,TransferError,inspect_transfer,restore_new_install,_db_checks
from backup_policy import AUTO_PATTERN,cross_process_lock

DEVICE=re.compile(r"ContractsIP-backup-[a-f0-9]{32}")
MANUAL=re.compile(r"complete-[a-f0-9]{32}\.zip")
MAX_CANDIDATES=200
MAX_ZIP_BYTES=MAX_BYTES+64*1024*1024

def candidates(folder):
    root=Path(folder)
    if root.is_symlink() or not root.is_dir():
        raise TransferError("Папка резервирования недоступна")
    root=root.resolve(strict=True)
    found=[]
    def visit(directory):
        for item in directory.iterdir():
            if not (AUTO_PATTERN.fullmatch(item.name) or MANUAL.fullmatch(item.name)):
                continue
            # Include named broken archives for visible rejection, not links.
            found.append(item)
            if len(found)>MAX_CANDIDATES:
                raise TransferError("Более 200 архивов: выберите папку отдельного компьютера")
    visit(root)
    for child in root.iterdir():
        if DEVICE.fullmatch(child.name) and not child.is_symlink() and child.is_dir():
            visit(child)
    return sorted(found,key=lambda x:str(x))

@contextmanager
def local_snapshot(archive):
    src=Path(archive)
    if src.is_symlink() or not src.is_file() or src.suffix.lower()!=".zip":
        raise TransferError("Укажите существующий ZIP-файл без символической ссылки")
    with TemporaryDirectory(prefix="contracts-recovery-") as temp:
        copy=Path(temp)/"archive.zip"
        size=0
        with src.open("rb") as inp,copy.open("xb") as out:
            before=os.fstat(inp.fileno())
            if before.st_size>MAX_ZIP_BYTES:raise TransferError("Слишком большой архив")
            while chunk:=inp.read(1024*1024):
                size+=len(chunk)
                if size>MAX_ZIP_BYTES:raise TransferError("Слишком большой архив")
                out.write(chunk)
            out.flush();os.fsync(out.fileno())
            after=os.fstat(inp.fileno())
            if (size!=before.st_size or size!=after.st_size or
                before.st_mtime_ns!=after.st_mtime_ns):
                raise TransferError("Архив изменился при чтении; дождитесь синхронизации")
        yield copy

def review_copy(copy):
    info=inspect_transfer(copy)
    try:
        dt=datetime.fromisoformat(info["createdUtc"])
        if dt.tzinfo is None:raise ValueError("timezone")
        dt=dt.astimezone(timezone.utc)
        if not isinstance(info["archiveId"],str) or uuid.UUID(info["archiveId"]).hex!=info["archiveId"]:
            raise ValueError("archive ID")
    except (ValueError,TypeError,KeyError) as exc:
        raise TransferError("Некорректная дата или ID архива") from exc
    return {**info,"createdUtc":dt.isoformat()}

def review_archive(archive):
    with local_snapshot(archive) as copy:
        return review_copy(copy)

def scan_archives(folder):
    good=[];bad=[]
    for path in candidates(folder):
        try:
            info=review_archive(path)
            good.append({"path":str(path),"size":path.stat().st_size,
                         **info})
        except Exception as exc:
            bad.append({"path":str(path),"valid":False,"error":type(exc).__name__})
    # Manifest dates are used, not file modification time from cloud sync.
    good.sort(key=lambda x:(x["createdUtc"],x["archiveId"],x["path"]),reverse=True)
    return {"valid":good,"invalid":bad,"recommended":good[0] if good else None}

def restore_archive(archive,target):
    # Restore from the exact private snapshot that was reviewed. A later
    # cloud change cannot alter the bytes between validation and extraction.
    with local_snapshot(archive) as copy:
        info=review_copy(copy)
        result=restore_new_install(copy,target)
        if not result.get("appReady") or result["archiveId"]!=info["archiveId"]:
            raise TransferError("Восстановление не подтверждено")
        return result

def restore_first_install(archive,target):
    target=Path(target)
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise TransferError("Каталог уже содержит данные")
    # Verify before removing even an empty directory.
    with local_snapshot(archive) as copy:
        review_copy(copy)
        if target.exists():target.rmdir()
        target.parent.mkdir(parents=True,exist_ok=True)
        return restore_new_install(copy,target)

def restore_separate(archive,base):
    base=Path(base)
    base.parent.mkdir(parents=True,exist_ok=True)
    target=base.with_name(base.name+"-Recovered-"+uuid.uuid4().hex)
    result=restore_archive(archive,target)
    return target,result

def control_dir(base):
    base=Path(base)
    path=base.with_name(base.name+".control")
    if path.is_symlink():raise TransferError("Недопустимый каталог управления")
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    if not path.is_dir():raise TransferError("Недопустимый каталог управления")
    return path

def _valid_target(base,name):
    base=Path(base)
    if not isinstance(name,str) or not re.fullmatch(re.escape(base.name)+r"-Recovered-[a-f0-9]{32}",name):
        raise TransferError("Некорректный путь восстановленной установки")
    path=base.with_name(name)
    marker=path/".transfer-verified.json"
    db=path/"lab.db"
    if path.is_symlink() or not path.is_dir() or marker.is_symlink() or db.is_symlink() or not db.is_file():
        raise TransferError("Восстановленная установка недоступна")
    try:
        proof=json.loads(marker.read_text(encoding="utf-8"))
        if proof["verified"] is not True or uuid.UUID(proof["archiveId"]).hex!=proof["archiveId"]:
            raise ValueError("marker")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise TransferError("Маркер восстановления недействителен") from exc
    return path

def active_data_root(base):
    base=Path(base)
    control=base.with_name(base.name+".control")
    if control.is_symlink():raise TransferError("Недопустимый каталог управления")
    pointer=control/"active-install.json"
    if pointer.is_symlink():raise TransferError("Недопустимая ссылка настройки установки")
    if not pointer.exists():return base
    try:
        obj=json.loads(pointer.read_text(encoding="utf-8"))
        return _valid_target(base,obj["active"])
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise TransferError("Настройка активной базы повреждена. Пустая база не создаётся.") from exc

def activate_recovered(base,target):
    """Call ONLY after the old app is stopped, under installation lock."""
    base=Path(base);target=Path(target)
    valid=_valid_target(base,target.name)
    if target!=valid:raise TransferError("Каталог восстановления не относится к установке")
    _db_checks(valid/"lab.db")
    previous=active_data_root(base)
    control=control_dir(base)
    pointer=control/"active-install.json"
    tmp=pointer.with_name(pointer.name+".pending-"+uuid.uuid4().hex)
    try:
        with tmp.open("x",encoding="utf-8") as stream:
            json.dump({"active":valid.name,"previous":previous.name},stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,pointer)
    finally:tmp.unlink(missing_ok=True)
    return valid

@contextmanager
def installation_lock(base):
    with cross_process_lock(control_dir(base)):
        yield
