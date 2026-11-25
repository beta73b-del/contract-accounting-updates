"""HTTP smoke on a throwaway SQLite database and random local port (Windows compatible)."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import http.client
import sqlite3
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
import urllib.error
import uuid

ROOT = Path(__file__).resolve().parent.parent

def call(url, payload=None, key="", origin=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key: headers["X-Contracts-Key"] = key
    if origin: headers["Origin"] = origin
    request = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request,timeout=6) as res:
            return res.status, res.read().decode("utf-8"), dict(res.headers)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, exc.read().decode("utf-8"), dict(exc.headers)
        finally:
            exc.close()

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory(prefix="sqlite-http-lab-")
        cls.db = Path(cls.tmp.name) / "isolated.sqlite"
        with closing(socket.socket()) as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.url = f"http://127.0.0.1:{cls.port}"
        env = os.environ.copy()
        env.update(CONTRACTS_SQLITE_LAB_TEST="1", CONTRACTS_SQLITE_LAB_DB=str(cls.db),
                   CONTRACTS_SQLITE_LAB_PORT=str(cls.port), PYTHONDONTWRITEBYTECODE="1")
        flags = getattr(subprocess,"CREATE_NO_WINDOW",0)
        cls.server = subprocess.Popen([sys.executable,str(ROOT/"server.py")],
                                       env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
        cls.key = None
        for _ in range(110):
            if cls.server.poll() is not None:break
            try:
                status,text,_=call(cls.url+"/api/bootstrap")
                if status==200:
                    cls.key=json.loads(text)["nonce"];break
            except OSError:
                pass
            time.sleep(0.1)
        if not cls.key:
            cls.server.terminate();cls.server.wait(timeout=4);cls.tmp.cleanup()
            raise AssertionError("Isolated HTTP server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        try: cls.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.server.kill();cls.server.wait(timeout=5)
        cls.tmp.cleanup()

    def req(self,path,payload=None,key=None,origin=None):
        status,text,headers=call(self.url+path,payload,key if key is not None else self.key,origin)
        return status, json.loads(text), headers

    def test_01_static_and_api_start(self):
        status,text,headers=call(self.url+"/")
        self.assertEqual(status,200)
        self.assertIn("ИЗОЛИРОВАННАЯ ТЕСТОВАЯ ВЕРСИЯ",text)
        self.assertIn("Content-Security-Policy",headers)
        code,data,_=self.req("/api/bootstrap")
        self.assertEqual((code,data["storage"],data["migration"]),(200,"SQLite","NOT RUN"))
        self.assertEqual(data["records"],[])

    def test_02_auth_and_origin_guard(self):
        status,text,_=call(self.url+"/api/product",{"name":"DANGER"},key="")
        self.assertEqual(status,403)
        status,text,_=call(self.url+"/api/product",{"name":"DANGER"},key=self.key,origin="https://invalid.example")
        self.assertEqual(status,403)

    def test_02b_reject_dns_rebinding_host_before_nonce_or_write(self):
        def hostile(method,path,body=None):
            connection=http.client.HTTPConnection("127.0.0.1",self.port,timeout=5)
            try:
                connection.putrequest(method,path,skip_host=True)
                connection.putheader("Host","malicious.invalid:"+str(self.port))
                if body is not None:
                    connection.putheader("Content-Type","application/json")
                    connection.putheader("Content-Length",str(len(body)))
                    connection.putheader("X-Contracts-Key",self.key)
                connection.endheaders(body)
                response=connection.getresponse()
                return response.status,json.loads(response.read().decode("utf-8"))
            finally:
                connection.close()
        code,data=hostile("GET","/api/bootstrap")
        self.assertEqual(code,403)
        self.assertNotIn("nonce",data)
        code,data=hostile("POST","/api/product",b'{"name":"blocked"}')
        self.assertEqual(code,403)
        code,data,_=self.req("/api/bootstrap")
        self.assertEqual(code,200)
        self.assertFalse(any(x.get("product")=="blocked" for x in data["stock"]["items"]))

    def test_03_complete_lifecycle_and_backup(self):
        p="Рутокен Lite 1010"
        status,_,_=self.req("/api/product",{"name":p})
        self.assertEqual(status,200)
        op="rctp-"+uuid.uuid4().hex
        receipt={"requestId":op,"product":p,"qty":10,"unitCost":"1000,05"}
        status,one,_=self.req("/api/receipt",receipt)
        self.assertEqual(status,200)
        status,two,_=self.req("/api/receipt",receipt)
        self.assertEqual(one,two)
        cid="test-"+uuid.uuid4().hex
        record={"id":cid,"revision":0,"status":"формирование","customer":"Тестовая организация",
                "platform":"Тест ЭТП","contractNumber":"00001","contractDate":"",
                "signBy":"","fulfillBy":"","handoverDate":"","paymentDue":"",
                "items":[{"product":p,"quantity":"3","unitPrice":"1500.25"}]}
        def save():
            payload={"requestId":"save-"+uuid.uuid4().hex,"record":record}
            return self.req("/api/record",payload)
        status,draft,_=save()
        self.assertEqual((status,draft["revision"]),(200,1))
        record["revision"]=draft["revision"];record["status"]="заключен"
        status,signed,_=save()
        self.assertEqual(status,200)
        self.assertRegex(signed["contract_date"],r"^\d{4}-\d{2}-\d{2}$")
        record["revision"]=signed["revision"];record["status"]="Отправлен"
        status,sent,_=save()
        self.assertEqual(status,200)
        code,bootstrap,_=self.req("/api/bootstrap")
        self.assertEqual(code,200)
        item=next(x for x in bootstrap["stock"]["items"] if x["product"]==p)
        self.assertEqual(item["physical"],7)
        record["revision"]=sent["revision"];record["status"]="Вручен";record["handoverDate"]="2026-11-20"
        status,delivered,_=save()
        self.assertEqual(status,200)
        record["revision"]=delivered["revision"];record["status"]="Оплачен"
        status,error,_=save()
        self.assertEqual(status,400)
        self.assertIn("Оплату",error["error"])
        record["paymentConfirmed"]=True
        status,paid,_=save()
        self.assertEqual(status,200)
        code,bootstrap,_=self.req("/api/bootstrap")
        item=next(x for x in bootstrap["stock"]["items"] if x["product"]==p)
        self.assertEqual(item["physical"],7,"repeat delivery/payment must not ship twice")
        self.assertEqual(bootstrap["integrity"]["integrity"],"ok")
        code,backup,_=self.req("/api/backup",{})
        self.assertEqual(code,200)
        dest=Path(backup["file"])
        self.assertTrue(dest.exists())
        self.assertTrue(dest.resolve().is_relative_to(self.db.parent.resolve()))
        self.assertEqual(len(backup["sha256"]),64)


    def test_04_finance_api_crud_rate_and_revision(self):
        exp_id=uuid.uuid4().hex
        create={"action":"create","opId":uuid.uuid4().hex,"id":exp_id,
                "revision":0,"date":"2026-11-24","amount":"150.01","comment":"Synthetic logistics"}
        status,added,_=self.req("/api/finance",create)
        self.assertEqual(status,200)
        self.assertTrue(any(x["id"]==exp_id for x in added["expenses"]))
        replay_status,replay,_=self.req("/api/finance",create)
        self.assertEqual(replay_status,200)
        self.assertEqual(replay,added)
        edit=dict(create,action="edit",opId=uuid.uuid4().hex,revision=1,amount="175.99")
        status,edited,_=self.req("/api/finance",edit)
        self.assertEqual(status,200)
        self.assertEqual(next(x for x in edited["expenses"] if x["id"]==exp_id)["revision"],2)
        status,error,_=self.req("/api/finance",dict(edit,opId=uuid.uuid4().hex,revision=1))
        self.assertEqual(status,400)
        self.assertIn("измен",error["error"])
        rate={"action":"rate","opId":uuid.uuid4().hex,"year":2027,"rate":15,"previous":None}
        status,changed,_=self.req("/api/finance",rate)
        self.assertEqual(status,200)
        self.assertEqual(changed["rates"]["2027"],15)
        code,boot,_=self.req("/api/bootstrap")
        self.assertEqual(code,200)
        self.assertEqual(boot["finance"]["rates"]["2027"],15)
        remove={"action":"delete","opId":uuid.uuid4().hex,"id":exp_id,"revision":2}
        status,removed,_=self.req("/api/finance",remove)
        self.assertEqual(status,200)
        self.assertFalse(any(x["id"]==exp_id for x in removed["expenses"]))
        # The immutable event journal is part of the sqlite archive.
        code,archive,_=self.req("/api/backup",{})
        self.assertEqual(code,200)
        with closing(sqlite3.connect(archive["file"])) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM finance_events").fetchone()[0],4)


    def test_05_trash_restore_guards_and_expense_recovery(self):
        product="Рутокен Lite 1010"
        status,_,_=self.req("/api/product",{"name":product})
        self.assertEqual(status,200)
        status,_,_=self.req("/api/receipt",{"requestId":"receipt-trash-http",
             "product":product,"qty":10,"unitCost":"1000.00"})
        self.assertEqual(status,200)
        cid="trash-http-"+uuid.uuid4().hex
        draft={"id":cid,"revision":0,"status":"формирование",
               "customer":"Тестовый заказчик","platform":"Тест","contractNumber":"001",
               "contractDate":"","signBy":"","fulfillBy":"","handoverDate":"","paymentDue":"",
               "items":[{"product":product,"quantity":"6","unitPrice":"1300.00"}]}
        status,first,_=self.req("/api/record",{"requestId":"save-"+uuid.uuid4().hex,"record":draft})
        self.assertEqual(status,200)
        draft["status"]="заключен";draft["revision"]=first["revision"]
        status,signed,_=self.req("/api/record",{"requestId":"save-"+uuid.uuid4().hex,"record":draft})
        self.assertEqual(status,200)
        code,pre,_=self.req("/api/bootstrap")
        self.assertEqual(code,200)
        row=next(x for x in pre["stock"]["items"] if x["product"]==product)
        self.assertEqual(row["reserved"],6)
        remove={"requestId":"trash-"+uuid.uuid4().hex,"id":cid,
                "expectedRevision":signed["revision"],"reason":"Ошибка"}
        status,deleted,_=self.req("/api/trash",remove)
        self.assertEqual(status,200)
        self.assertEqual(deleted["action"],"trash")
        status,repeat,_=self.req("/api/trash",remove)
        self.assertEqual((status,repeat),(200,deleted))
        status,boot,_=self.req("/api/bootstrap")
        self.assertEqual(status,200)
        self.assertFalse(any(x["id"]==cid for x in boot["records"]))
        self.assertTrue(any(x["id"]==cid for x in boot["trash"]["contracts"]))
        self.assertEqual(next(x["reserved"] for x in boot["stock"]["items"] if x["product"]==product),0)
        status,error,_=self.req("/api/restore",{"requestId":"restore-stale-"+uuid.uuid4().hex,
                              "id":cid,"expectedRevision":signed["revision"]})
        self.assertEqual(status,409)
        status,restored,_=self.req("/api/restore",{
               "requestId":"restore-"+uuid.uuid4().hex,"id":cid,
               "expectedRevision":deleted["revision"]})
        self.assertEqual(status,200)
        status,after,_=self.req("/api/bootstrap")
        self.assertEqual(status,200)
        self.assertTrue(any(x["id"]==cid for x in after["records"]))
        self.assertEqual(len(after["trash"]["contracts"]),0)
        self.assertEqual(next(x["reserved"] for x in after["stock"]["items"] if x["product"]==product),6)
        draft["status"]="Отправлен";draft["revision"]=restored["revision"]
        status,shipped,_=self.req("/api/record",{"requestId":"ship-"+uuid.uuid4().hex,"record":draft})
        self.assertEqual(status,200)
        status,error,_=self.req("/api/trash",{
               "requestId":"trash-forbidden-"+uuid.uuid4().hex,
               "id":cid,"expectedRevision":shipped["revision"]})
        self.assertEqual(status,400)
        code,boot,_=self.req("/api/bootstrap")
        self.assertEqual(code,200)
        initial_physical=next(x["physical"] for x in pre["stock"]["items"] if x["product"]==product)
        final_physical=next(x["physical"] for x in boot["stock"]["items"] if x["product"]==product)
        self.assertEqual(final_physical,initial_physical-6,
                         "Shipment must deduct only this contract, despite prior integration tests")
        expid=uuid.uuid4().hex
        create={"action":"create","opId":uuid.uuid4().hex,"id":expid,
                "revision":0,"date":"2026-11-24","amount":"11.57","comment":"Synthetic"}
        status,e,_=self.req("/api/finance",create);self.assertEqual(status,200)
        status,e,_=self.req("/api/finance",{"action":"delete","opId":uuid.uuid4().hex,
                                          "id":expid,"revision":1})
        self.assertEqual(status,200)
        self.assertEqual(e["deletedExpenses"][0]["revision"],2)
        status,e,_=self.req("/api/finance",{"action":"restore","opId":uuid.uuid4().hex,
                                          "id":expid,"revision":2})
        self.assertEqual(status,200)
        self.assertEqual(e["expenses"][0]["amount"],"11.57")
        self.assertEqual(e["deletedExpenses"],[])
        status,backup,_=self.req("/api/backup",{})
        self.assertEqual(status,200)
        with closing(sqlite3.connect(backup["file"])) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contract_lifecycle").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contracts WHERE id=?",(cid,)).fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
