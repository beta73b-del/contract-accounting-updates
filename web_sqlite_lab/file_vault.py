"""Managed documents and 30-day reversible file trash for isolated SQLite LAB.

Only files managed by this application's storage service are intercepted.
Operating-system deletions outside app-owned directories cannot be captured.

Design: immutable opaque blobs; deleting toggles SQLite state, not file bytes.
A crash before SQLite commit leaves (at worst) an unreferenced blob, not a
missing active document. At >=30 days, a two-phase purge removes the blob,
retaining non-sensitive metadata and an append-only audit trail.

No production Excel, external directories, or application releases touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import os
import re
import uuid

from store import Conflict, DataError, utcnow
from transfer_package import _safe_name, TransferError

RETENTION_DAYS = 30
MAX_FILE_BYTES = 256 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{8,90}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")

def _now(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value,str):
        value=datetime.fromisoformat(value)
    if not isinstance(value,datetime) or value.tzinfo is None:
        raise DataError("Дата должна содержать часовой пояс")
    return value.astimezone(timezone.utc)

def _name(name,allow_relative=False):
    try:
        _safe_name(name)
    except TransferError as exc:
        raise DataError("Недопустимое имя документа") from exc
    # Permit a normalized virtual relative path for legacy attachments.
    # Physical data is always stored as an opaque ID.bin, never under name.
    if len(name)>800 or ("/" in name and not allow_relative) or name in ("manifest.json","database.sqlite"):
        raise DataError("Имя документа должно быть допустимым именем файла")
    return name

def _id(value, what="идентификатор"):
    if not isinstance(value,str) or _ID.fullmatch(value) is None:
        raise DataError("Неверный "+what)
    return value

class FileVault:
    def __init__(self,store,root=None):
        self.store=store
        self.root=Path(root if root is not None else store.path.parent/"managed_files")
        if self.root.is_symlink():
            raise DataError("Каталог документов не может быть символьной ссылкой")
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.root=self.root.resolve()
        if not self.root.is_dir():
            raise DataError("Недоступен каталог документов")

    def _blob(self, file_id, row=None):
        _id(file_id, "ID документа")
        name = (row["blob"] if row is not None else file_id+".bin")
        if name != file_id+".bin":
            raise DataError("Повреждено имя файла в базе")
        path=self.root/name
        if path.is_symlink():
            raise DataError("Подмена документа символьной ссылкой запрещена")
        return path

    def _row(self,file_id):
        _id(file_id,"ID документа")
        row=self.store.db.execute("SELECT * FROM managed_files WHERE id=?",(file_id,)).fetchone()
        if row is None:raise DataError("Документ не найден")
        return row

    @staticmethod
    def _public(row):
        return {"id":row["id"],"name":row["name"],"contractId":row["contract_id"],
                "size":row["size_bytes"],"sha256":row["sha256"],
                "state":row["state"],"revision":row["revision"],
                "createdAt":row["created_at"],"deletedAt":row["deleted_at"],
                "expiresAt":row["expires_at"],"reason":row["reason"]}

    def list_files(self,state="active"):
        if state not in ("active","trashed"):
            raise DataError("Недопустимый раздел файлов")
        rows=self.store.db.execute(
            "SELECT * FROM managed_files WHERE state=? ORDER BY created_at DESC,id DESC",
            (state,)).fetchall()
        return [self._public(row) for row in rows]

    def _check_blob(self,row):
        path=self._blob(row["id"],row)
        if not path.is_file():
            raise DataError("Файл отсутствует в хранилище, требуется восстановление из архива")
        if not _HASH.fullmatch(row["sha256"]) or path.stat().st_size!=row["size_bytes"]:
            raise DataError("Размер или контрольная сумма документа изменены")
        h=sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda:fh.read(1024*1024),b""):
                h.update(chunk)
        if h.hexdigest()!=row["sha256"]:
            raise DataError("Контрольная сумма документа не совпадает")
        return path

    def get_active(self,file_id):
        row=self._row(file_id)
        if row["state"]!="active":
            raise DataError("Документ находится в Корзине или срок хранения истёк")
        return self._public(row),self._check_blob(row)

    def add_stream(self, request_id, file_id, name, stream, size, contract_id=None,allow_relative=False):
        """Store an uploaded stream without trusting client file paths or MIME.

        Uploads must use unique IDs. Repeated identical operations return the
        previous result; changing request content with same ID is rejected.
        """
        _id(request_id,"ID операции")
        _id(file_id,"ID документа")
        name=_name(name,allow_relative=allow_relative)
        if isinstance(size,bool) or type(size)!=int or not (0<=size<=MAX_FILE_BYTES):
            raise DataError("Файл превышает 256 МБ или неверно указан размер")
        if contract_id in (None,""):
            contract_id=None
        else:
            _id(contract_id,"ID контракта")
        if not hasattr(stream,"read"):raise DataError("Неверный поток файла")
        temp=self.root/(file_id+".part-"+uuid.uuid4().hex)
        final=self._blob(file_id)
        digest=sha256()
        written=0
        linked=False
        try:
            with temp.open("xb") as fh:
                remaining=size
                while remaining:
                    part=stream.read(min(1024*1024,remaining))
                    if not isinstance(part,bytes) or not part:
                        raise DataError("Передача файла прервана")
                    fh.write(part);digest.update(part)
                    remaining-=len(part);written+=len(part)
                fh.flush();os.fsync(fh.fileno())
            if written!=size:raise DataError("Неполный файл")
            payload={"kind":"file:create","id":file_id,"name":name,
                     "bytes":size,"sha256":digest.hexdigest(),
                     "contractId":contract_id}
            with self.store.transaction():
                op_hash,old=self.store._operation(request_id,payload)
                if old is not None:
                    return old
                if self.store.db.execute(
                        "SELECT 1 FROM managed_files WHERE id=?",(file_id,)).fetchone():
                    raise Conflict("Документ с таким идентификатором уже существует")
                if contract_id:
                    self.store.get_contract(contract_id)
                os.link(temp,final)
                linked=True
                created=utcnow()
                self.store.db.execute("""INSERT INTO managed_files(
                    id,name,blob,contract_id,size_bytes,sha256,state,revision,
                    created_at,deleted_at,expires_at,purged_at,reason)
                    VALUES(?,?,?,?,?,?,'active',1,?,NULL,NULL,NULL,'')""",
                    (file_id,name,file_id+".bin",contract_id,size,digest.hexdigest(),created))
                self.store.db.execute(
                    "INSERT INTO file_events(file_id,request_id,action,happened_at,revision)"
                    " VALUES(?,?, 'create',?,1)",
                    (file_id,request_id,created))
                result=self._public(self._row(file_id))
                self.store._record_op(request_id,op_hash,result)
            return result
        except BaseException:
            if linked:
                # Existing DB entry may have been committed by another actor:
                # never delete a referenced blob after an ambiguous failure.
                if not self.store.db.execute(
                    "SELECT 1 FROM managed_files WHERE id=?",(file_id,)).fetchone():
                    final.unlink(missing_ok=True)
            raise
        finally:
            temp.unlink(missing_ok=True)

    def _change(self,action,request_id,file_id,revision,reason="",now=None):
        _id(request_id,"ID операции")
        _id(file_id,"ID документа")
        if type(revision)!=int or not 1<=revision<=999999999:
            raise DataError("Не указана актуальная ревизия документа")
        if not isinstance(reason,str) or len(reason)>300:
            raise DataError("Причина удаления слишком длинная")
        when=_now(now)
        payload={"kind":"file:"+action,"id":file_id,"revision":revision,
                 "reason":reason if action=="trash" else ""}
        with self.store.transaction():
            op_hash,old=self.store._operation(request_id,payload)
            if old is not None:return old
            row=self._row(file_id)
            if row["revision"]!=revision:
                raise Conflict("Документ изменён в другом окне")
            if action=="trash":
                if row["state"]!="active":
                    raise Conflict("Документ уже удалён")
                self._check_blob(row)
                expiry=when+timedelta(days=RETENTION_DAYS)
                self.store.db.execute("""UPDATE managed_files
                    SET state='trashed',revision=revision+1,deleted_at=?,
                        expires_at=?,reason=? WHERE id=?""",
                    (when.isoformat(),expiry.isoformat(),reason,file_id))
            elif action=="restore":
                if row["state"]!="trashed":
                    raise Conflict("Документ недоступен для восстановления")
                if when>=_now(row["expires_at"]):
                    raise DataError("Срок хранения 30 дней истёк")
                if row["contract_id"]:
                    # A linked contract must be restored first.
                    self.store.get_contract(row["contract_id"])
                self._check_blob(row)
                self.store.db.execute("""UPDATE managed_files
                    SET state='active',revision=revision+1,
                        deleted_at=NULL,expires_at=NULL,reason=''
                    WHERE id=?""",(file_id,))
            else:
                raise DataError("Неизвестная операция")
            self.store.db.execute(
                "INSERT INTO file_events(file_id,request_id,action,happened_at,revision)"
                " VALUES(?,?,?,?,?)",(file_id,request_id,action,when.isoformat(),revision+1))
            result=self._public(self._row(file_id))
            self.store._record_op(request_id,op_hash,result)
            return result

    def trash_file(self,request_id,file_id,revision,reason="",now=None):
        return self._change("trash",request_id,file_id,revision,reason,now)

    def restore_file(self,request_id,file_id,revision,now=None):
        return self._change("restore",request_id,file_id,revision,now=now)

    def purge_expired(self,now=None):
        """Purge ONLY file bytes whose 30-day deadline is reached.

        No contract, financial transaction, warehouse movement, or event
        history is ever erased by this scheduler. On crash, purge_pending is
        resumed on next call. Source file deletions outside vault are forbidden.
        """
        moment=_now(now)
        rows=self.store.db.execute("""SELECT id FROM managed_files WHERE
            state='purge_pending' OR (state='trashed' AND expires_at<=?)
            ORDER BY expires_at,id""",(moment.isoformat(),)).fetchall()
        purged=0
        for entry in rows:
            fid=entry["id"]
            row=self._row(fid)
            if row["state"]=="trashed":
                # Detect corruption BEFORE changing state. Do not erase a
                # tampered or suspicious path.
                self._check_blob(row)
                with self.store.transaction():
                    current=self._row(fid)
                    if current["state"]!="trashed" or _now(current["expires_at"])>moment:
                        continue
                    self.store.db.execute(
                        "UPDATE managed_files SET state='purge_pending',revision=revision+1 WHERE id=?",
                        (fid,))
                    self.store.db.execute(
                        "INSERT INTO file_events(file_id,request_id,action,happened_at,revision)"
                        " VALUES(?,?,'expire',?,?)",
                        (fid,"expire-"+uuid.uuid4().hex,moment.isoformat(),current["revision"]+1))
            row=self._row(fid)
            if row["state"]!="purge_pending":
                continue
            path=self._blob(fid,row)
            if path.is_file():
                # A resumed purge after a crash may see the old file. Confirm
                # content matches the registered blob before deleting bytes.
                self._check_blob(row)
                path.unlink()
            with self.store.transaction():
                current=self._row(fid)
                if current["state"]!="purge_pending":
                    continue
                self.store.db.execute(
                    "UPDATE managed_files SET state='purged',revision=revision+1,purged_at=?"
                    " WHERE id=?",(moment.isoformat(),fid))
                self.store.db.execute(
                    "INSERT INTO file_events(file_id,request_id,action,happened_at,revision)"
                    " VALUES(?,?,'purge',?,?)",
                    (fid,"purge-"+uuid.uuid4().hex,moment.isoformat(),current["revision"]+1))
                purged+=1
        return {"purgedFiles":purged,"checkedAt":moment.isoformat(),
                "retentionDays":RETENTION_DAYS}

    def trash_legacy_attachment(self,request_id,relative_path,attachments_root,contract_id=None):
        """Protect a pre-existing app-owned attachment on explicit deletion.

        Copy+verify -> register -> trash -> remove original. Crashes can leave
        duplicates; they must never leave the only copy deleted. Not for
        arbitrary OS files, symlinks, or paths outside attachments_root.
        """
        _id(request_id,"ID операции")
        try:_safe_name(relative_path)
        except TransferError as exc:raise DataError("Неверный путь вложения") from exc
        base=Path(attachments_root)
        if base.is_symlink() or not base.is_dir():
            raise DataError("Недоступен каталог вложений")
        base=base.resolve(strict=True)
        src=base.joinpath(*relative_path.split("/"))
        # Reject symlink at every path component, even when it resolves inside.
        node=base
        for component in relative_path.split("/"):
            node=node/component
            if node.is_symlink():
                raise DataError("Символьные ссылки во вложениях запрещены")
        # Deterministic IDs make a repeated delete request idempotent even
        # if the original file was already removed after successful copying.
        file_id=sha256((request_id+"\\0"+relative_path).encode("utf-8")).hexdigest()[:32]
        prior=self.store.db.execute("SELECT * FROM managed_files WHERE id=?",
                                    (file_id,)).fetchone()
        if prior is not None and prior["state"] in ("trashed","purged") and not src.exists():
            return self._public(prior)
        if not src.is_file() or not src.resolve().is_relative_to(base):
            raise DataError("Файл вложения отсутствует")
        copy_op=sha256(("copy:"+request_id).encode("utf-8")).hexdigest()
        trash_op=sha256(("trash:"+request_id).encode("utf-8")).hexdigest()
        with src.open("rb") as stream:
            item=self.add_stream(copy_op,file_id,relative_path,
                                 stream,src.stat().st_size,contract_id,allow_relative=True)
        if item["state"]=="trashed":
            trashed=item
        else:
            trashed=self.trash_file(trash_op,item["id"],item["revision"],
                                    "Удалён файл из прежнего каталога вложений")
        # Verify the protected byte stream and the original by SHA-256.
        # Never load a potentially 256-MB attachment fully into memory.
        protected=self._check_blob(self._row(item["id"]))
        source_hash=sha256()
        with src.open("rb") as stream:
            for block in iter(lambda:stream.read(1024*1024),b""):
                source_hash.update(block)
        if (src.is_symlink() or source_hash.hexdigest()!=self._row(item["id"])["sha256"]
                or protected.stat().st_size!=src.stat().st_size):
            raise DataError("Исходный файл изменился во время копирования")
        src.unlink()
        return trashed
