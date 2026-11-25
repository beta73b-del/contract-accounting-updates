"""CI synthetic archive travel: Windows -> Mac and Mac -> Windows.

Usage:
  python tests/cross_os_fixture.py export path/to/artifact.zip
  python tests/cross_os_fixture.py verify path/to/artifact.zip

The payload contains no user data, credentials or real contracts.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import sys
import uuid

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from store import Store
from file_vault import FileVault
from finance_ledger import FinanceLedger
from transfer_package import export_lab,restore_new_install,inspect_transfer

def identity():
    return uuid.uuid4().hex

def create(archive):
    archive=Path(archive).resolve()
    if archive.exists():raise FileExistsError("Never overwrite transfer artifacts")
    with TemporaryDirectory(prefix="CrossOS-Synthetic-Sender-") as dirname:
        root=Path(dirname)
        source=root/"sender"
        source.mkdir()
        docs=source/"attachments"
        (docs/"сопроводительные письма").mkdir(parents=True)
        (docs/"сопроводительные письма"/"Контракт №42.txt").write_bytes(
            "Только синтетические документы. №42".encode("utf-8"))
        db=Store(source/"lab.db")
        try:
            db.add_product("Рутокен Lite 1010")
            db.receipt(identity(),"Рутокен Lite 1010",20,"1250.37")
            cid="synthetic-contract-0042"
            result=db.save_contract(identity(),{
                "id":cid,"revision":0,"status":"формирование",
                "customer":"Вымышленный заказчик","platform":"Тестовая ЭТП",
                "contractNumber":"42/2026",
                "contractDate":"","signBy":"","fulfillBy":"","handoverDate":"","paymentDue":"",
                "items":[{"product":"Рутокен Lite 1010","quantity": "7","unitPrice":"1450.10"}]
            })
            result=db.save_contract(identity(),{
                "id":cid,"revision":result["revision"],"status":"заключен",
                "customer":"Вымышленный заказчик","platform":"Тестовая ЭТП",
                "contractNumber":"42/2026",
                "contractDate":"2026-11-25","signBy":"","fulfillBy":"","handoverDate":"",
                "paymentDue":"","items":[{"product":"Рутокен Lite 1010",
                  "quantity":"7","unitPrice":"1450.10"}]})
            vault=FileVault(db)
            active=vault.add_stream(identity(),identity(),"Формуляр.pdf",
                                    BytesIO(b"%PDF-1.4 example FORM 3120"),26,cid)
            trash=vault.add_stream(identity(),identity(),"Ошибка подписи.docx",
                                   BytesIO(b"synthetic deleted DOCX"),22)
            vault.trash_file(identity(),trash["id"],1,"test user accidentally deleted")
            FinanceLedger(db).mutate({"action":"create","opId":identity(),"id":identity(),
                "date":"2026-11-25","amount":"100.40","comment":"Synthetic common expense",
                "revision":0})
            assert db.stock_summary()[0]["reserved"]==7
            exported=export_lab(db.path,docs,archive)
            assert exported["managedBlobs"]==2
            assert exported["documents"]==3
            return {"archiveSha256":__import__("hashlib").sha256(archive.read_bytes()).hexdigest(),
                    "counts":exported["database"]["counts"]}
        finally:
            db.close()

def verify(archive):
    archive=Path(archive).resolve(strict=True)
    with TemporaryDirectory(prefix="CrossOS-Synthetic-Recipient-") as dirname:
        root=Path(dirname)
        installed=root/"NewComputerContractsIPSqliteLab"
        before=inspect_transfer(archive)
        result=restore_new_install(archive,installed)
        assert result["appReady"] and not result["existingInstallOverwritten"]
        assert not (installed/"database.sqlite").exists()
        assert (installed/"attachments"/"сопроводительные письма"/"Контракт №42.txt").read_bytes()==(
            "Только синтетические документы. №42".encode("utf-8"))
        store=Store(installed/"lab.db")
        try:
            assert store.db.execute("PRAGMA user_version").fetchone()[0]==4
            assert store.verify()["foreignKeys"]==0
            assert len(store.list_contracts())==1
            assert store.list_contracts()[0]["contract_number"]=="42/2026"
            assert store.stock_summary()[0]["physical"]==20
            assert store.stock_summary()[0]["reserved"]==7
            file_vault=FileVault(store)
            active=file_vault.list_files("active")
            trash=file_vault.list_files("trashed")
            assert len(active)==len(trash)==1
            assert file_vault.get_active(active[0]["id"])[1].read_bytes()==b"%PDF-1.4 example FORM 3120"
            assert file_vault._blob(trash[0]["id"]).read_bytes()==b"synthetic deleted DOCX"
            restored=file_vault.restore_file(identity(),trash[0]["id"],trash[0]["revision"])
            assert file_vault.get_active(restored["id"])[1].read_bytes()==b"synthetic deleted DOCX"
            expense=FinanceLedger(store).payload()["expenses"]
            assert len(expense)==1 and expense[0]["amount"]=="100.40"
            assert before["database"]["counts"]["managed_files"]==2
            assert result["managedBlobs"]==2
            return {"verified":True,"recoveredActive":len(active),"recoveredTrashed":len(trash),
                    "contractCount":len(store.list_contracts()),"expenseCount":len(expense)}
        finally:store.close()

if __name__=="__main__":
    if len(sys.argv)!=3 or sys.argv[1] not in ("export","verify"):
        raise SystemExit("Usage: python cross_os_fixture.py export|verify archive.zip")
    result=create(sys.argv[2]) if sys.argv[1]=="export" else verify(sys.argv[2])
    print(json.dumps(result,sort_keys=True))
