"""Persist a mounted backup path outside the active data directory."""
from pathlib import Path
import json
import os
import uuid
from transfer_package import TransferError

class BackupSettings:
    def __init__(self, root):
        self.root=Path(root).resolve(strict=True)
        self.file=self.root/"backup-settings.json"

    def read(self):
        if self.file.is_symlink():
            raise TransferError("Недопустимая ссылка файла настроек")
        if not self.file.exists():
            return {"folder":None,"deviceId":None,"legacy":True}
        try:
            d=json.loads(self.file.read_text(encoding="utf-8"))
            folder,device=d["folder"],d["deviceId"]
            if not isinstance(device,str) or uuid.UUID(device).hex!=device:
                raise ValueError("deviceId")
            if folder is not None and (not isinstance(folder,str) or not Path(folder).is_absolute()):
                raise ValueError("folder")
            return {"folder":folder,"deviceId":device,"legacy":False}
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise TransferError("Не удалось прочитать настройки резервирования") from error

    def _folder(self, raw):
        if not isinstance(raw,str) or not raw or not Path(raw).is_absolute():
            raise TransferError("Укажите абсолютный путь к существующей папке")
        try:
            path=Path(raw)
            if path.is_symlink() or not path.is_dir():
                raise TransferError("Папка резервирования недоступна")
            path=path.resolve(strict=True)
        except OSError as error:
            raise TransferError("Папка резервирования недоступна") from error
        if path==self.root or path.is_relative_to(self.root):
            raise TransferError("Внешнее хранилище не может находиться внутри каталога данных")
        return path

    def destination(self, data):
        if data["legacy"]:
            raw=os.environ.get("CONTRACTS_SQLITE_LAB_SECONDARY_BACKUP_DIR")
            return self._folder(raw) if raw else None
        if data["folder"] is None:return None
        child=self._folder(data["folder"])/("ContractsIP-backup-"+data["deviceId"])
        if child.is_symlink() or not child.is_dir():
            raise TransferError("Папка внешних копий недоступна")
        return child.resolve(strict=True)

    def save(self, raw):
        old=self.read()
        device=old["deviceId"] or uuid.uuid4().hex
        if raw is None or raw=="":folder=None
        else:
            parent=self._folder(raw)
            folder=str(parent)
            child=parent/("ContractsIP-backup-"+device)
            try:
                child.mkdir(mode=0o700,exist_ok=True)
                if child.is_symlink() or not child.is_dir():
                    raise TransferError("Недопустимая папка внешних копий")
                probe=child/(".write-test-"+uuid.uuid4().hex)
                owned=False
                try:
                    with probe.open("xb") as f:
                        owned=True
                        f.write(b"ContractsIP backup probe")
                        f.flush();os.fsync(f.fileno())
                    if probe.read_bytes()!=b"ContractsIP backup probe":
                        raise TransferError("Проверка записи не пройдена")
                finally:
                    if owned:probe.unlink(missing_ok=True)
            except OSError as error:
                raise TransferError("Нет доступа к записи во внешнее хранилище") from error
        tmp=self.file.with_name(self.file.name+".pending-"+uuid.uuid4().hex)
        try:
            with tmp.open("x",encoding="utf-8") as f:
                json.dump({"folder":folder,"deviceId":device},f,ensure_ascii=False)
                f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.file)
        finally:tmp.unlink(missing_ok=True)
        return self.read()
