"""Portable ZIP export, preflight and restore-to-EMPTY-folder for the SQLite lab.

No production activation. Never overwrites existing user data. Cross-platform
stdlib only. A complete archive requires all explicitly linked documents.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path,PurePosixPath
from tempfile import TemporaryDirectory
import json
import os
import re
import shutil
import sqlite3
import stat
import unicodedata
import uuid
import zipfile

FORMAT="contracts-ip-transfer-v1"
MAX_FILES=10000
MAX_BYTES=2_000_000_000
MAX_RATIO=1000
DATA_NAME="database.sqlite"
MANIFEST_NAME="manifest.json"

class TransferError(ValueError):pass

def _sha(data):
    return sha256(data).hexdigest()

def _sha_file(path):
    h=sha256()
    with open(path,"rb") as stream:
        for part in iter(lambda:stream.read(1024*1024),b""):h.update(part)
    return h.hexdigest()

def _safe_name(name):
    if not isinstance(name,str) or not name or len(name)>800 or "\\" in name or "\x00" in name:
        raise TransferError("Invalid archive entry")
    p=PurePosixPath(name)
    if p.is_absolute() or name.startswith("/") or any(x in ("",".","..") for x in name.split("/")):
        raise TransferError("Unsafe archive entry")
    if re.match(r"^[A-Za-z]:",name):
        raise TransferError("Windows drive path not permitted")
    # Archive must be usable on both NTFS and APFS: reject aliases/ADS,
    # trailing Windows dot/space and reserved device basenames.
    for segment in name.split("/"):
        if segment.endswith((" ", ".")) or any(x in segment for x in '<>:"|?*'):
            raise TransferError("Windows-incompatible archive filename")
        stem=segment.split(".",1)[0]
        if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])",stem):
            raise TransferError("Reserved Windows device filename")
    return name

def _files_tree(folder):
    folder=Path(folder).resolve(strict=True)
    if not folder.is_dir():raise TransferError("Attachments must be a directory")
    entries=[]
    for p in sorted(folder.rglob("*")):
        if p.is_symlink():raise TransferError("Document symlinks are prohibited")
        if p.is_file():
            rel=p.relative_to(folder).as_posix()
            _safe_name(rel)
            entries.append((p,"attachments/"+rel))
    return entries

def _db_checks(db):
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise TransferError("SQLite integrity_check failed")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise TransferError("SQLite foreign_key_check failed")
        version=int(con.execute("PRAGMA user_version").fetchone()[0])
        if version not in (1,2,3):raise TransferError("Unsupported SQLite schema")
        counts={}
        for table in ("contracts","contract_lines","products","receipts","shipments","operations")+ (("finance_events",) if version >= 2 else ()) + (("contract_trash","contract_lifecycle") if version >= 3 else ()):
            row=con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
            if not row or not row[0]:raise TransferError("Required schema table is absent")
            counts[table]=con.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0]
        return {"schemaVersion":version,"counts":counts}

def export_lab(db_path,attachments_dir,archive_path,required_documents=()):
    """Create immutable zip; caller must supply actual referenced documents."""
    db=Path(db_path).resolve(strict=True)
    archive=Path(archive_path).resolve()
    folder=Path(attachments_dir).resolve(strict=True)
    if archive.exists():raise FileExistsError("Never overwrite an existing transfer archive")
    if not db.is_file() or not folder.is_dir():raise TransferError("Database/documents missing")
    documents=_files_tree(folder)
    doc_names={name[len("attachments/"):] for _,name in documents}
    for doc in required_documents:
        if not isinstance(doc,str):raise TransferError("Invalid required document name")
        _safe_name(doc)
        if doc not in doc_names:raise TransferError("Missing required linked document")
    if len(documents)+2>MAX_FILES:raise TransferError("Too many documents")
    archive.parent.mkdir(parents=True,exist_ok=True)
    stage=archive.with_name(archive.name+".incomplete-"+uuid.uuid4().hex)
    try:
        with TemporaryDirectory(prefix="contracts-export-") as temp:
            snap=Path(temp)/DATA_NAME
            with closing(sqlite3.connect(db.as_uri()+"?mode=ro",uri=True)) as source:
                with closing(sqlite3.connect(snap)) as dest:
                    source.backup(dest)
            metadata=_db_checks(snap)
            files=[(snap,DATA_NAME)]+documents
            if sum(path.stat().st_size for path,_ in files)>MAX_BYTES:
                raise TransferError("Transfer size limit exceeded")
            seen=set();manifest_files=[]
            for p,name in files:
                _safe_name(name)
                normalized=unicodedata.normalize("NFC",name).casefold()
                if normalized in seen:raise TransferError("Cross-platform filename collision")
                seen.add(normalized)
                manifest_files.append({"path":name,"size":p.stat().st_size,"sha256":_sha_file(p)})
            manifest={"format":FORMAT,"createdUtc":datetime.now(timezone.utc).isoformat(),
                      "archiveId":uuid.uuid4().hex,"database":metadata,
                      "requiredDocuments":sorted(set(required_documents)),
                      "files":manifest_files,"complete":True}
            with zipfile.ZipFile(stage,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as zf:
                for p,name in files:zf.write(p,name)
                zf.writestr(MANIFEST_NAME,json.dumps(manifest,ensure_ascii=False,sort_keys=True))
        # Never silently replace target (including concurrent process).
        fd=os.open(archive,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            with os.fdopen(fd,"wb") as out,open(stage,"rb") as source:
                shutil.copyfileobj(source,out,1024*1024)
                out.flush();os.fsync(out.fileno())
            # Do not leave a published ZIP that fails its own manifest check.
            # If a document changed during compression, this check rejects it.
            return inspect_transfer(archive)
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
    finally:
        stage.unlink(missing_ok=True)

def inspect_transfer(archive_path):
    archive=Path(archive_path).resolve(strict=True)
    with zipfile.ZipFile(archive,"r") as zf:
        members=zf.infolist()
        if len(members)>MAX_FILES:raise TransferError("Too many archive entries")
        names=set();total=0
        for item in members:
            name=_safe_name(item.filename)
            folded=unicodedata.normalize("NFC",name).casefold()
            if folded in names:raise TransferError("Duplicate or cross-platform name collision")
            names.add(folded)
            if item.is_dir():raise TransferError("Directories are not permitted in archive")
            mode=(item.external_attr>>16)&0xffff
            if stat.S_IFMT(mode)==stat.S_IFLNK:raise TransferError("Symlinks are not allowed")
            total+=item.file_size
            if total>MAX_BYTES:raise TransferError("Archive expanded size limit")
            if item.file_size>0 and (item.compress_size==0 or item.file_size/item.compress_size>MAX_RATIO):
                raise TransferError("Suspicious ZIP compression ratio")
        if MANIFEST_NAME.casefold() not in names or DATA_NAME.casefold() not in names:
            raise TransferError("Archive has no manifest/database")
        try:
            manifest=json.loads(zf.read(MANIFEST_NAME))
        except (ValueError,UnicodeError) as e:
            raise TransferError("Invalid archive manifest") from e
        if manifest.get("format")!=FORMAT or manifest.get("complete") is not True:
            raise TransferError("Unsupported or incomplete transfer format")
        files=manifest.get("files")
        if not isinstance(files,list) or not files:raise TransferError("No manifest files")
        listed={}
        for entry in files:
            name=_safe_name(entry.get("path"))
            if name in listed:raise TransferError("Duplicate file in manifest")
            listed[name]=entry
        if set(listed)|{MANIFEST_NAME} != {x.filename for x in members}:
            raise TransferError("Archive files do not match manifest")
        required=manifest.get("requiredDocuments")
        if not isinstance(required,list):raise TransferError("Invalid required file list")
        if any("attachments/"+_safe_name(x) not in listed for x in required):
            raise TransferError("Missing required linked document")
        with TemporaryDirectory(prefix="contracts-import-review-") as tmp:
            db=Path(tmp)/DATA_NAME
            for name,entry in listed.items():
                info=zf.getinfo(name)
                if info.file_size!=entry.get("size") or not isinstance(entry.get("sha256"),str):
                    raise TransferError("Manifest file size/hash metadata mismatch")
                h=sha256()
                with zf.open(name) as source:
                    if name==DATA_NAME:
                        with open(db,"wb") as sink:
                            for part in iter(lambda:source.read(1024*1024),b""):
                                h.update(part);sink.write(part)
                    else:
                        for part in iter(lambda:source.read(1024*1024),b""):h.update(part)
                if h.hexdigest()!=entry["sha256"]:raise TransferError("Hash mismatch")
            db_metadata=_db_checks(db)
        if db_metadata != manifest.get("database"):
            raise TransferError("SQLite row counts/schema changed")
        return {"valid":True,"archiveId":manifest["archiveId"],
                "files":len(files),"documents":len(files)-1,
                "database":db_metadata,"createdUtc":manifest["createdUtc"]}

def restore_to_empty(archive_path,target_dir):
    """Safe validation and restore only to a NONEXISTENT folder.

    Does not replace an installed application's database. Live cutover and
    rollback are separate, not implemented here.
    """
    preflight=inspect_transfer(archive_path)
    requested=Path(target_dir)
    if requested.is_symlink():
        raise TransferError("Destination symlink is prohibited")
    target=requested.resolve()
    if not target.parent.is_dir():
        raise TransferError("Destination parent must exist")
    # Create destination exclusively *before* extraction. On POSIX,
    # Path.rename(stage, target) could otherwise replace a directory
    # another process created after a final exists() check.
    target.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(archive_path,"r") as zf:
            manifest=json.loads(zf.read(MANIFEST_NAME))
            for entry in manifest["files"]:
                rel=_safe_name(entry["path"])
                out=target/rel
                if not out.resolve().is_relative_to(target):
                    raise TransferError("Destination path escapes target")
                out.parent.mkdir(parents=True,exist_ok=True)
                with zf.open(rel) as src,open(out,"xb") as dest:
                    shutil.copyfileobj(src,dest,1024*1024)
                if _sha_file(out)!=entry["sha256"]:
                    raise TransferError("Restore SHA mismatch")
        if _db_checks(target/DATA_NAME)!=preflight["database"]:
            raise TransferError("Restore database mismatch")
        # A marker signals completed, fully verified transfer-only staging.
        # No active application is switched to this directory by this code.
        (target/".transfer-verified.json").write_text(
            json.dumps({"archiveId":preflight["archiveId"],"verified":True},
                       sort_keys=True),encoding="utf-8")
        return {**preflight,"restoredToEmpty":True}
    except BaseException:
        # Only a destination created exclusively by this process is removed.
        shutil.rmtree(target,ignore_errors=True)
        raise
