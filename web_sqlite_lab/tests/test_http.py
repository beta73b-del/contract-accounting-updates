"""HTTP smoke on a throwaway SQLite database and random local port (Windows compatible)."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import json
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
        self.assertTrue(str(dest).startswith(str(self.db.parent)))
        self.assertEqual(len(backup["sha256"]),64)

if __name__=="__main__":
    unittest.main()
