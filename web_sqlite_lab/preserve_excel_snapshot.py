"""Read-only preservation snapshot of active Excel Web API and linked files.

PRIVATE local output only; no production writes or GitHub uploads.
It is *not* yet a complete normalized migration and does not change lab.db.
"""
from __future__ import annotations
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen
from datetime import datetime, timezone
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid

def checksum(p):
    h=sha256()
    with open(p,"rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()

def snapshot(source, destination, api="http://localhost:8765/api/bootstrap", max_total_bytes=2_000_000_000):
    source=Path(source).resolve(strict=True)
    destination=Path(destination).resolve()
    if destination == source or source in destination.parents:
        raise ValueError("The private snapshot must be outside the production directory")
    if destination.exists():raise FileExistsError(destination)
    staging=destination.with_name(destination.name+".staging-"+uuid.uuid4().hex[:8])
    staging.mkdir(parents=True)
    copied_bytes=0
    entries=[]
    warnings=[]
    def copy(src,relative):
        nonlocal copied_bytes
        src=Path(src)
        if src.is_symlink():raise ValueError("Symbolic links not allowed in transfer: "+str(src))
        if not src.is_file():raise FileNotFoundError(src)
        target=staging/relative
        if not target.resolve().is_relative_to(staging.resolve()):
            raise ValueError("Unexpected path traversal")
        size=src.stat().st_size
        copied_bytes+=size
        if copied_bytes>max_total_bytes:raise ValueError("Snapshot exceeds configured size limit")
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,target)
        s1=checksum(src);s2=checksum(target)
        if s1!=s2:raise ValueError("Source changed during copying: "+str(src))
        entries.append({"path":relative.as_posix(),"size":size,"sha256":s2})
    try:
        with urlopen(api,timeout=30) as r:
            payload=json.load(r)
        expected={"records","stock","finance","notes"}
        if not expected.issubset(payload):raise ValueError("Missing API sections")
        allowed={k:payload[k] for k in ("records","stock","finance","notes","owner") if k in payload}
        ids=[r["id"] for r in allowed["records"]]
        if len(ids)!=len(set(ids)):raise ValueError("Duplicate contract IDs in source")
        (staging/"source_api.json").write_text(json.dumps(allowed,ensure_ascii=False,indent=2),encoding="utf-8")
        entries.append({"path":"source_api.json","size":(staging/"source_api.json").stat().st_size,
                        "sha256":checksum(staging/"source_api.json")})
        # Copy the workbook as an additional source; Excel can block reads while open.
        copy(source/"data"/"База_контрактов.xlsx",Path("source")/"База_контрактов.xlsx")
        for name in ("notes.json","competitors.json","contract-folders.json"):
            copy(source/"data"/name,Path("source")/name)
        drafts=source/"data"/"letter-drafts"
        for f in sorted(drafts.rglob("*")):
            if f.is_file():copy(f,Path("source")/"letter-drafts"/f.relative_to(drafts))
        folder_index=json.loads((source/"data"/"contract-folders.json").read_text(encoding="utf-8-sig"))
        folders=folder_index.get("entries",[])
        if not isinstance(folders,list):raise ValueError("Invalid linked folder list")
        for obj in folders:
            identity=obj.get("id")
            raw=obj.get("path")
            if not isinstance(identity,str) or not re.fullmatch(r"[A-Za-z0-9_-]{5,128}",identity) or not isinstance(raw,str):
                raise ValueError("Invalid linked document folder reference")
            docs_root=(source.parent/"Документы").resolve()
            if raw.startswith("docs:"):
                relative=raw[5:].replace("\\","/")
                if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                    raise ValueError("Invalid portable document folder")
                root=(docs_root/relative).resolve()
            else:
                root=Path(raw).resolve()
            if not root.is_relative_to(docs_root):
                raise ValueError("Linked document folder outside source documents root")
            if not root.is_dir():
                warnings.append({"contractId":identity,"error":"MISSING_DOCUMENT_FOLDER"})
                continue
            for file in sorted(root.rglob("*")):
                if file.is_symlink():raise ValueError("Document symlink rejected")
                if file.is_file():
                    copy(file,Path("linked-documents")/identity/file.relative_to(root))
        database=staging/"preserved_raw.sqlite"
        with closing(sqlite3.connect(database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE raw_contracts(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
            db.execute("CREATE TABLE raw_sections(name TEXT PRIMARY KEY,payload TEXT NOT NULL)")
            db.executemany("INSERT INTO raw_contracts VALUES (?,?)",[
                (r["id"],json.dumps(r,ensure_ascii=False,sort_keys=True)) for r in allowed["records"]])
            for key in ("stock","finance","notes","owner"):
                if key in allowed:
                    db.execute("INSERT INTO raw_sections VALUES (?,?)",
                               (key,json.dumps(allowed[key],ensure_ascii=False,sort_keys=True)))
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise ValueError("Raw SQLite integrity failed")
            if db.execute("SELECT COUNT(*) FROM raw_contracts").fetchone()[0]!=len(ids):
                raise ValueError("Raw contract count mismatch")
        entries.append({"path":"preserved_raw.sqlite","size":database.stat().st_size,"sha256":checksum(database)})
        manifest={"format":"contracts-ip-preservation-v1","createdUtc":datetime.now(timezone.utc).isoformat(),
                  "contracts":len(ids),"linkedFolders":len(folders),"draftJsonFiles":len(list(drafts.rglob("*.json"))),
                  "documentsCopied":len([e for e in entries if e["path"].startswith("linked-documents/")]),
                  "warnings":warnings,"files":entries,
                  "status":"PARTIAL" if warnings else "SOURCE_COPY_VERIFIED",
                  "limitations":["API excludes deleted historical records","Consistency of a live Excel COM workbook with API not proven",
                                 "Historical Excel sheets and formulas not yet parsed/reconciled",
                                 "This is a preservation snapshot, not complete normalized migration"]}
        (staging/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        # Verify all files before finalizing snapshot.
        for entry in entries:
            p=staging/entry["path"]
            if p.stat().st_size!=entry["size"] or checksum(p)!=entry["sha256"]:
                raise ValueError("Snapshot verification failed")
        staging.rename(destination)
        return {k:manifest[k] for k in ("contracts","linkedFolders","draftJsonFiles","documentsCopied","status","warnings")}
    except BaseException:
        shutil.rmtree(staging,ignore_errors=True)
        raise

if __name__=="__main__":
    if len(sys.argv)!=3:
        raise SystemExit("Usage: python preserve_excel_snapshot.py <production-app-dir> <new-private-snapshot-dir>")
    result=snapshot(sys.argv[1],sys.argv[2])
    print(json.dumps(result,ensure_ascii=False))
