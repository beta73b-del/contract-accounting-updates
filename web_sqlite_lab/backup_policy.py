"""Verified low-frequency complete backups for the isolated ContractsIP SQLite LAB.

Policy:
* At most one automatic CHECK per 24 hours while the app runs.
* Keep a new COMPLETE archive if data changed, or refresh unchanged data
  every 7 days to avoid having no recent recovery point.
* Prune only files owned by this policy, and only after verifying a good copy.
  Keep a minimum of one verified recovery point. Retention = 14 days.
* Never prune manual exports, pre-migration snapshots or an arbitrary folder.
* Optional second physical destination receives an identical SHA-verified ZIP.
  Independence of physical device/immutable storage must be verified by user.

This is a prototype, NOT a production backup of the existing Excel app.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from zipfile import ZipFile
import json
import os
import re
import shutil
import uuid

from transfer_package import export_lab,inspect_transfer,TransferError

RETENTION_DAYS=14
CHECK_HOURS=24
REFRESH_DAYS=7
AUTO_PATTERN=re.compile(r"^auto-(\d{8}T\d{6}Z)-([a-f0-9]{32})\.zip$")
_lock=Lock()

def aware_utc(value=None):
    value=datetime.now(timezone.utc) if value is None else value
    if not isinstance(value,datetime) or value.tzinfo is None:
        raise ValueError("Backup timestamp must have a timezone")
    return value.astimezone(timezone.utc)

def file_sha256(path):
    h=sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()

def canonical_signature(path):
    """Stable digest of validated archive's CONTENT, ignoring ZIP timestamps."""
    with ZipFile(path) as zf:
        man=json.loads(zf.read("manifest.json"))
    rows=sorted((x["path"],x["size"],x["sha256"]) for x in man["files"])
    return sha256(json.dumps(rows,ensure_ascii=False,
                             separators=(",",":")).encode("utf-8")).hexdigest()

@contextmanager
def cross_process_lock(folder):
    """Nonblocking advisory lock: no stranded .running directory on crash."""
    path=Path(folder)/".automatic-backup.lock"
    if path.is_symlink():raise TransferError("Backup lock symlink is prohibited")
    fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,"O_NOFOLLOW",0),0o600)
    with os.fdopen(fd,"r+b",buffering=0) as stream:
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b"\0")
        stream.seek(0)
        if os.name=="nt":
            import msvcrt
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as error:
                raise TransferError("Another backup is already running") from error
        else:
            import fcntl
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error:
                raise TransferError("Another backup is already running") from error
        try:yield
        finally:
            stream.seek(0)
            if os.name=="nt":
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(stream,fcntl.LOCK_UN)

class BackupPolicy:
    def __init__(self, db_path, attachments_dir=None, managed_dir=None,
                 backup_dir=None, secondary_dir=None):
        self.db=Path(db_path).resolve(strict=True)
        self.root=self.db.parent
        self.attachments=Path(attachments_dir or self.root/"attachments")
        self.managed=Path(managed_dir or self.root/"managed_files")
        self.backups=Path(backup_dir or self.root/"backups"/"automatic")
        if self.backups.is_symlink():
            raise TransferError("Automatic backup folder symlink prohibited")
        self.backups.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.backups=self.backups.resolve(strict=True)
        self.secondary=None
        if secondary_dir:
            dest=Path(secondary_dir)
            if dest.is_symlink() or not dest.is_dir():
                raise TransferError("Secondary backup destination must be an existing regular folder")
            dest=dest.resolve(strict=True)
            if dest==self.backups or dest==self.root or dest.is_relative_to(self.root):
                raise TransferError("Secondary backup must not be inside active app data")
            self.secondary=dest
        self.state_file=self.backups/".automatic-backup-state.json"

    def _state(self):
        if self.state_file.is_symlink():
            raise TransferError("Backup state symlink prohibited")
        if not self.state_file.exists():return {}
        try:
            obj=json.loads(self.state_file.read_text(encoding="utf-8"))
            return obj if isinstance(obj,dict) else {}
        except (OSError,ValueError,UnicodeError):
            return {}

    def _save_state(self,data):
        dest=self.state_file
        tmp=dest.with_name(dest.name+".pending-"+uuid.uuid4().hex)
        try:
            with tmp.open("x",encoding="utf-8") as stream:
                json.dump(data,stream,ensure_ascii=False,sort_keys=True)
                stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,dest)
        finally:
            tmp.unlink(missing_ok=True)

    def _entries(self,directory):
        result=[]
        for path in directory.iterdir():
            m=AUTO_PATTERN.fullmatch(path.name)
            if not m:continue
            if path.is_symlink() or not path.is_file():
                raise TransferError("Non-regular automatic backup entry")
            try:
                when=datetime.strptime(m.group(1),"%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            result.append((when,path))
        return sorted(result,key=lambda item:(item[0],item[1].name))

    def _new_archive(self,moment):
        return self.backups/("auto-"+moment.strftime("%Y%m%dT%H%M%SZ")
                            +"-"+uuid.uuid4().hex+".zip")

    def _copy_secondary(self,source):
        if self.secondary is None:return "NOT_CONFIGURED"
        target=self.secondary/source.name
        if target.is_symlink():raise TransferError("Secondary backup symlink prohibited")
        digest=file_sha256(source)
        if target.exists():
            if file_sha256(target)!=digest:
                raise TransferError("Existing secondary archive differs; never overwrite")
            inspect_transfer(target)
            return "VERIFIED"
        stage=self.secondary/(target.name+".staging-"+uuid.uuid4().hex)
        try:
            with source.open("rb") as inp,stage.open("xb") as out:
                shutil.copyfileobj(inp,out,1024*1024)
                out.flush();os.fsync(out.fileno())
            if file_sha256(stage)!=digest:raise TransferError("Secondary SHA-256 mismatch")
            os.link(stage,target)  # No overwriting, same secondary filesystem.
            inspect_transfer(target)
            return "VERIFIED"
        finally:stage.unlink(missing_ok=True)

    def _prune(self,directory,moment,preserve):
        entries=self._entries(directory)
        removed=0
        for created,path in entries:
            if path.name==preserve:continue
            if created+timedelta(days=RETENTION_DAYS)>moment:continue
            # Strictly policy-owned archive names in a dedicated directory.
            # Never delete manual 'complete-*.zip' or before-v*.sqlite.
            path.unlink()
            removed+=1
        return removed

    def status(self):
        state=self._state()
        entries=self._entries(self.backups)
        return {"primaryPath":str(self.backups),
                "secondaryPath":str(self.secondary) if self.secondary else None,
                "secondaryConfigured":self.secondary is not None,
                "retentionDays":RETENTION_DAYS,
                "checkHours":CHECK_HOURS,
                "lastCheckUtc":state.get("lastCheckUtc"),
                "lastVerifiedUtc":state.get("lastVerifiedUtc"),
                "lastArchive":state.get("lastArchive"),
                "lastSecondaryStatus":state.get("secondaryStatus","NOT_CONFIGURED"),
                "automaticCopies":len(entries),
                "warning":("NO_VERIFIED_COPY" if not entries else
                           "SECONDARY_NOT_CONFIGURED" if self.secondary is None else None)}

    def run_if_due(self,now=None,force=False):
        moment=aware_utc(now)
        with _lock:
            with cross_process_lock(self.backups):
                state=self._state()
                last_check=state.get("lastCheckUtc")
                try:
                    last_check=datetime.fromisoformat(last_check) if last_check else None
                    last_check=aware_utc(last_check) if last_check else None
                except (ValueError,TypeError):
                    last_check=None
                if (not force and last_check is not None and
                    last_check<=moment and
                    moment-last_check<timedelta(hours=CHECK_HOURS)):
                    return {"status":"NOT_DUE",**self.status()}
                entries=self._entries(self.backups)
                last_good=None
                if entries:
                    for created,path in reversed(entries):
                        try:
                            inspect_transfer(path)
                            last_good=(created,path)
                            break
                        except (TransferError,OSError,ValueError,KeyError):
                            continue
                archive=self._new_archive(moment)
                result=export_lab(self.db,self.attachments,archive,
                                  managed_dir=self.managed)
                if not result.get("valid"):raise TransferError("Automatic archive not verified")
                changed=True
                if last_good:
                    old_created,old_path=last_good
                    changed=(canonical_signature(old_path)!=canonical_signature(archive))
                    if not changed and moment-old_created<timedelta(days=REFRESH_DAYS):
                        archive.unlink()  # Only this new, fully verified archive.
                        archive=old_path
                secondary_status="NOT_CONFIGURED"
                if self.secondary is not None:
                    try:secondary_status=self._copy_secondary(archive)
                    except (OSError,TransferError,ValueError):
                        secondary_status="FAILED"
                # Only prune after a COMPLETE archive or a verified existing one.
                if not archive.is_file():
                    raise TransferError("Verified archive unexpectedly missing")
                inspect_transfer(archive)
                cleaned=self._prune(self.backups,moment,archive.name)
                if secondary_status=="VERIFIED":
                    self._prune(self.secondary,moment,archive.name)
                self._save_state({
                    "lastCheckUtc":moment.isoformat(),
                    "lastVerifiedUtc":moment.isoformat(),
                    "lastArchive":archive.name,
                    "secondaryStatus":secondary_status,
                    "lastContentSignature":canonical_signature(archive),
                })
                return {"status":"CREATED" if changed or not last_good or
                                     archive!=last_good[1] else "UNCHANGED",
                        "pruned":cleaned,
                        "archive":str(archive),
                        "secondaryStatus":secondary_status,
                        **self.status()}
