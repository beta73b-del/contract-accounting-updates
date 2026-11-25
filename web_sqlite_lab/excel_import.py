"""Local Excel/Web -> SQLite *trial* baseline. Never write the original installation.
Preserve original bytes, raw workbook, JSON and projected stock history. Only
fields represented in schema v4 become editable. No invented shipment/receipt.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from urllib.request import build_opener,ProxyHandler
import json,os,re,shutil,uuid

from preserve_excel_snapshot import snapshot,checksum
from source_audit import audit
from staging_import import import_snapshot
from stock_baseline import baseline,reservation_differences
from store import Store,STATUSES,SHIPPED,normalized_lines,validate_date,money,amount
from file_vault import FileVault
from finance_ledger import FinanceLedger
from transfer_package import export_lab,_safe_name,TransferError
from sqlite_runtime import sqlite3

API="http://localhost:8765/api/bootstrap"
REPORT="excel-import-report.json"
class MigrationError(ValueError):pass

def uid(*parts):
    return sha256(json.dumps(parts,ensure_ascii=False).encode()).hexdigest()[:32]

def decimal_text(x):
    if isinstance(x,bool) or x is None:raise MigrationError("Отсутствует денежное значение")
    if isinstance(x,float):
        # API JSON decimal -> exact textual representation, never round.
        return str(x)
    return str(x)

def pick(obj,keys,label,default=None):
    present=[obj[k] for k in keys if k in obj and obj[k] is not None]
    if not present:return default
    if any(v!=present[0] for v in present[1:]):raise MigrationError("Противоречивые поля: "+label)
    return present[0]

def textfield(obj,keys,label,limit):
    value=pick(obj,keys,label,"")
    if not isinstance(value,str) or len(value)>limit:raise MigrationError("Некорректное текстовое поле: "+label)
    return value

def money_values(obj,keys,issues,label):
    """Collect only exact, supported monetary values. Original values remain raw."""
    valid=[]
    for k in keys:
        if k not in obj or obj[k] is None or (isinstance(obj[k],str) and not obj[k].strip()):continue
        try:valid.append(money(decimal_text(obj[k])))
        except ValueError:issues.append(label+": недопустимый денежный формат "+k)
    if len(set(valid))>1:
        issues.append(label+": разные денежные значения")
        return None
    return valid[0] if valid else None

def source_lines(r):
    raw=pick(r,("items","positions"),"позиции")
    if raw is None:
        if all(k in r for k in ("product","quantity")):raw=[r]
        else:raise MigrationError("Неизвестный формат товарных позиций; требуется карта полей")
    if not isinstance(raw,list) or not raw:raise MigrationError("Нет товарных позиций")
    out=[];derived=[];issues=[];source_totals=[]
    declared=money_values(r,("amount",),issues,"Сумма контракта")
    for pos,x in enumerate(raw,1):
        if not isinstance(x,dict):raise MigrationError("Некорректная позиция")
        qty=pick(x,("quantity","qty"),"количество")
        label="Позиция "+str(pos)
        if not re.fullmatch(r"[1-9]\d{0,6}",str(qty)):
            raise MigrationError(label+": некорректное количество")
        q=int(qty)
        before=len(issues)
        price=money_values(x,("unitPrice","price"),issues,label+" цена")
        price_ambiguous=len(issues)!=before
        before=len(issues)
        line_total=money_values(x,("lineTotal","total","amount"),issues,label+" сумма")
        total_ambiguous=len(issues)!=before
        if len(raw)==1 and declared is not None:
            if line_total is None and not total_ambiguous:line_total=declared
            elif line_total is not None and line_total!=declared:
                issues.append(label+": сумма строки и контракта различаются")
        # Source permits amount-only lines: price stays blank, even when exactly divisible.
        # Never derive or invent unit price.
        if price is not None and price*q>90_000_000_000_000:
            issues.append(label+": произведение превышает предел; исходная цена сохранена в архиве")
            price=None
        if price is not None and line_total is not None and price*q!=line_total:
            issues.append(label+": цена × количество не равно исходной сумме")

        source_totals.append(line_total)
        line={"product":pick(x,("product","name"),"товар"),"quantity":qty,"unitPrice":amount(price) if price is not None else ""}
        if price is None and line_total is not None and not total_ambiguous:line["amount"]=amount(line_total)
        out.append(line)
    return normalized_lines(out,allow_missing_price=True),derived,issues,declared,source_totals

def normalize_contract(r):
    if not isinstance(r,dict):raise MigrationError("Некорректная карточка")
    cid=r.get("id")
    if not isinstance(cid,str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,90}",cid):
        raise MigrationError("Неподдерживаемый ID контракта")
    status=r.get("status")
    if status not in STATUSES:raise MigrationError("Неизвестный статус контракта: "+str(status))
    dates={k:validate_date(textfield(r,(k,),k,10)) for k in
        ("contractDate","signBy","fulfillBy","handoverDate","paymentDue")}
    lines,derived,issues,declared,source_totals=source_lines(r)
    missing=[i for i,l in enumerate(lines,1) if l.get("price_missing")]
    calculated=sum(x["line_total"] for x in lines)
    if declared is not None and declared!=calculated:
        issues.append("Исходная сумма контракта отличается от суммы известных цен")
    if declared is not None:
        total=declared
    elif all(t is not None for t in source_totals):
        total=sum(source_totals)
    elif all(not l.get("price_missing") or l.get("amount_explicit") for l in lines):
        total=calculated
    else:total=None
    # Missing prices on non-drafts cannot safely be changed via current editor.
    if any(l.get("price_missing") and not l.get("amount_explicit") for l in lines) and status not in ("формирование","подписать до","на подписи"):
        issues.append("Цена и сумма не заполнены в действующем или завершённом контракте")
    if total is None:issues.append("Сумма контракта не определена")
    financial={"sourceAmount":amount(declared) if declared is not None else None,
        "preservedAmount":amount(total) if total is not None else None,
        "calculatedAmount":amount(calculated) if all(not l.get("price_missing") or l.get("amount_explicit") for l in lines) else None,
        "lineAmounts":[amount(t) if t is not None else None for t in source_totals],
        "issues":issues,"missingPricePositions":missing,"derivedPricePositions":derived}
    return {"id":cid,"status":status,
        "customer":textfield(r,("customer",),"заказчик",500),
        "platform":textfield(r,("platform",),"площадка",120),
        "number":textfield(r,("contractNumber",),"номер",100),
        "dates":dates,"lines":lines,"total":total,"missingPrices":missing,"derivedPrices":derived,"financial":financial}

def read_api():
    opener=build_opener(ProxyHandler({}))
    with opener.open(API,timeout=30) as response:
        if response.geturl()!=API:raise MigrationError("Неожиданное перенаправление API")
        raw=response.read(64*1024*1024+1)
        if len(raw)>64*1024*1024:raise MigrationError("Снимок API слишком велик")
        obj=json.loads(raw)
    return {k:obj[k] for k in ("records","stock","finance","notes","owner") if k in obj}

def capture_source(source,dest):
    source=Path(source).resolve(strict=True)
    # Accept either the installation parent or the actual Приложение folder.
    if not (source/"data"/"База_контрактов.xlsx").is_file():
        source=source/"Приложение"
    workbook=source/"data"/"База_контрактов.xlsx"
    if not workbook.is_file():raise MigrationError("Не найдена data/База_контрактов.xlsx")
    before=read_api()
    # Use the existing preservation routine, fixed loopback URL.
    snapshot(source,dest,api=API)
    captured=json.loads((Path(dest)/"source_api.json").read_text(encoding="utf-8"))
    after=read_api()
    if before!=captured or captured!=after:
        raise MigrationError("Данные Excel изменились во время снимка. Повторите без операций в Excel.")
    manifest=json.loads((Path(dest)/"manifest.json").read_text(encoding="utf-8"))
    # Recheck all copied workbook/JSON/draft bytes against their source.
    for item in manifest["files"]:
        rel=item["path"]
        if rel.startswith("source/"):
            original=source/"data"/rel[len("source/"):]
            if not original.is_file() or checksum(original)!=item["sha256"]:
                raise MigrationError("Файлы Excel изменились во время снимка")
    return audit(dest)

def finance_baseline(store,source):
    if not isinstance(source,dict):raise MigrationError("Нет финансовых данных")
    ledger=FinanceLedger(store)
    rates=source.get("rates",{})
    if rates==[]:rates={}
    if not isinstance(rates,dict):raise MigrationError("Неизвестный формат ставок")
    expenses=source.get("expenses",[])
    deleted=source.get("deletedExpenses",[])
    if not isinstance(expenses,list) or not isinstance(deleted,list):raise MigrationError("Неизвестный формат расходов")
    mapping=[];seen=set()
    for year,rate in sorted(rates.items()):
        if str(year)=="2026":
            if int(rate)!=5:raise MigrationError("Ставка 2026 не совпадает с поддерживаемой")
            continue
        ledger.mutate({"action":"rate","opId":uid("rate",year),"year":year,"rate":rate,"previous":None})
    for group,trashed in ((expenses,False),(deleted,True)):
        for r in group:
            if not isinstance(r,dict) or not isinstance(r.get("id"),str) or not r["id"] or r["id"] in seen:
                raise MigrationError("Неверный/повторный ID расхода")
            sid=r["id"];seen.add(sid)
            eid=sid if re.fullmatch(r"[a-f0-9]{32}",sid) else uid("expense",sid)
            body={"action":"create","opId":uid("expense-create",sid),"id":eid,"revision":0,
                "date":r.get("date"),"amount":decimal_text(r.get("amount")),"comment":r.get("comment","")}
            ledger.mutate(body)
            if trashed:ledger.mutate({"action":"delete","opId":uid("expense-delete",sid),"id":eid,"revision":1})
            mapping.append({"sourceId":sid,"sqliteId":eid,"sourceRevision":r.get("revision"),"sqliteRevision":2 if trashed else 1})
    actual=ledger.payload()
    def vals(rows):
        return sorted((x["date"],money(decimal_text(x["amount"])),x.get("comment","")) for x in rows)
    expected_rates={str(k):int(v) for k,v in rates.items() if str(k)!="2026"}
    if (vals(expenses)!=vals(actual["expenses"]) or vals(deleted)!=vals(actual["deletedExpenses"])
        or actual["rates"]!=expected_rates):raise MigrationError("Расходы/ставки не совпали")
    return mapping,{"activeExpenses":len(expenses),"deletedExpenses":len(deleted),"rates":len(expected_rates),
        "expenseKopecks":sum(money(decimal_text(x["amount"])) for x in expenses)}

def build_trial_archive(snapshot_dir,archive_path):
    src=Path(snapshot_dir).resolve(strict=True)
    archive=Path(archive_path).resolve()
    if archive.is_relative_to(src) or archive.exists():raise MigrationError("Архив должен быть новым и вне источника")
    source_report=audit(src)
    checks=source_report["checks"]
    if any(checks[k] for k in ("inExcelNotAPI","inAPINotExcel","duplicateIds","contractStatusMismatches")):
        raise MigrationError("ID или статусы XLSX и API расходятся")
    manifest=json.loads((src/"manifest.json").read_text(encoding="utf-8"))
    raw=json.loads((src/"source_api.json").read_text(encoding="utf-8"))
    # Independent raw SQLite/JSON equality prevents selecting a mixed snapshot.
    with closing(sqlite3.connect((src/"preserved_raw.sqlite").as_uri()+"?mode=ro",uri=True)) as con:
        db_records={r[0]:json.loads(r[1]) for r in con.execute("SELECT id,payload FROM raw_contracts")}
        db_sections={r[0]:json.loads(r[1]) for r in con.execute("SELECT name,payload FROM raw_sections")}
    if db_records!={r["id"]:r for r in raw["records"]} or db_sections!={k:v for k,v in raw.items() if k!="records"}:
        raise MigrationError("JSON и сохранённый источник SQLite расходятся")
    opening=baseline(raw["stock"])
    records=[]
    for r in raw["records"]:
        try:records.append(normalize_contract(r))
        except (ValueError,TypeError) as exc:
            label=str(r.get("contractNumber") or r.get("id") or "?") if isinstance(r,dict) else "?"
            raise MigrationError("Контракт "+label+": "+str(exc)) from exc
    ids={r["id"] for r in records}
    if len(ids)!=len(records):raise MigrationError("Повторный контракт")
    archive.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix="excel-to-sqlite-",dir=archive.parent) as temp:
        root=Path(temp);attachments=root/"attachments"
        preserved=attachments/"Excel-source";preserved.mkdir(parents=True)
        # Preserve original full workbook, sidecars, linked document paths,
        # raw DB, manifest, even fields the trial UI does not support.
        for entry in manifest["files"]:
            name=_safe_name(entry["path"])
            f=src/name
            if f.is_symlink() or not f.is_file() or not f.resolve().is_relative_to(src):
                raise MigrationError("Небезопасный путь исходного файла")
            out=preserved/name;out.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(f,out)
            if out.stat().st_size!=entry["size"] or checksum(out)!=entry["sha256"]:
                raise MigrationError("Исходный файл изменился при переносе")
        shutil.copyfile(src/"manifest.json",preserved/"manifest.json")
        import_snapshot(preserved,preserved.parent/"raw-staging.sqlite")
        store=Store(root/"lab.db")
        try:
            now=datetime.now(timezone.utc).isoformat()
            inv=opening["inventory"];replay=opening["verifiedReplay"];phases=opening["phases"]
            stock_warnings=opening["warnings"]
            reserve_warnings=reservation_differences(inv,phases,records,opening["reservationReplay"])
            legacy_shipped=[];archived_deleted=[]
            with store.transaction():
                # Opening balances copied exactly from the current Web/Excel snapshot.
                # Historical replay is only an independent diagnostic.
                for name,item in inv.items():
                    if not isinstance(name,str) or not name or len(name)>200:raise MigrationError("Недопустимый товар")
                    if item["deleted"]:archived_deleted.append(name)
                    store.db.execute("INSERT INTO products VALUES(?,?,?)",(name,item["quantity"],item["cents"]))
                store.db.execute("INSERT INTO explicit_reservation_mode VALUES(1)")
                if reserve_warnings:
                    store.db.executemany("INSERT INTO stock_import_guard VALUES(?,?)",[(name,item["reserved"]) for name,item in inv.items()])
                store.db.execute("CREATE TABLE excel_import_raw_contracts(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)")
                store.db.execute("CREATE TABLE excel_import_stock_events(seq INTEGER PRIMARY KEY,payload_json TEXT NOT NULL)")
                for n,e in enumerate(raw["stock"]["events"],1):
                    store.db.execute("INSERT INTO excel_import_stock_events VALUES(?,?)",(n,json.dumps(e,ensure_ascii=False)))
                for source,r in zip(raw["records"],records):
                    cid=r["id"];d=r["dates"]
                    if any(l["product"] not in inv for l in r["lines"]):raise MigrationError("Товар карточки отсутствует в складском справочнике")
                    store.db.execute("INSERT INTO contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid,1,r["customer"],r["platform"],r["status"],r["number"],d["contractDate"],d["signBy"],d["fulfillBy"],d["handoverDate"],d["paymentDue"],now,now))
                    store.db.executemany("INSERT INTO contract_lines VALUES(?,?,?,?,?)",
                        [(cid,i,l["product"],l["quantity"],l["unit_price"]) for i,l in enumerate(r["lines"],1)])
                    store.db.executemany("INSERT INTO contract_line_missing_price VALUES(?,?)",[(cid,i) for i in r["missingPrices"]])
                    store.db.executemany("INSERT INTO contract_line_amounts VALUES(?,?,?)",[(cid,i+1,l["line_total"]) for i,l in enumerate(r["lines"]) if l.get("amount_explicit")])
                    store.db.execute("INSERT INTO contract_reserve_state VALUES(?,?)",(cid,int(phases.get(cid)=="reserved")))
                    store.db.execute("INSERT INTO excel_import_raw_contracts VALUES(?,?)",(cid,json.dumps(source,ensure_ascii=False)))
                    store.db.execute("INSERT INTO contract_import_financial VALUES(?,?)",(cid,json.dumps(r["financial"],ensure_ascii=False)))
                    phase=replay["contracts"].get(cid,{}) if replay is not None else {}
                    if r["status"] in SHIPPED:
                        shipment=phase.get("shipment")
                        if not shipment:
                            # Old fulfilled records without confirmed shipment:
                            # preserve status, do NOT fabricate a historic issue.
                            legacy_shipped.append(cid)
                        else:
                            qty={}
                            for l in r["lines"]:qty[l["product"]]=qty.get(l["product"],0)+l["quantity"]
                            actual={}
                            for l in shipment["lines"]:
                                product=l["product"];q=-l["quantity"];cost=-l["cents"]
                                if q<=0 or cost<0:raise MigrationError("Некорректная историческая отгрузка")
                                old=actual.get(product,(0,0));actual[product]=(old[0]+q,old[1]+cost)
                            if {k:v[0] for k,v in actual.items()}!=qty:
                                stock_warnings.append({"kind":"shipmentCardMismatch","id":cid})
                                legacy_shipped.append(cid)
                            else:
                                for product,(q,cost) in actual.items():
                                    store.db.execute("INSERT INTO shipments VALUES(?,?,?,?)",(cid,product,q,cost))
            # Strict quantity/value and per-product reserve reconciliation.
            actual={x["product"]:x for x in store.stock_summary()}
            if set(actual)!=set(inv):raise MigrationError("Номенклатура не совпала")
            for name,expected in inv.items():
                x=actual[name]
                if (x["physical"],money(x["inventoryCost"]),x["reserved"],x["available"],x["shortage"])!=(expected["quantity"],expected["cents"],expected["reserved"],expected["available"],expected["shortage"]):
                    raise MigrationError("Физический остаток, стоимость или резерв не совпали: "+name)
            got={x["id"]:x for x in store.list_contracts()}
            if set(got)!=ids:raise MigrationError("Набор карточек не совпал")
            for r in records:
                x=got[r["id"]];d=r["dates"]
                if (x["status"],x["customer"],x["platform"],x["contract_number"],x["contract_date"],x["sign_by"],x["fulfill_by"],x["handover_date"],x["payment_due"])!=(r["status"],r["customer"],r["platform"],r["number"],d["contractDate"],d["signBy"],d["fulfillBy"],d["handoverDate"],d["paymentDue"]):raise MigrationError("Реквизиты контракта не совпали")
                if [(l["product"],l["quantity"],money(l["unitPrice"]),money(l["amount"]),bool(l.get("priceMissing"))) for l in x["items"]]!=[(l["product"],l["quantity"],l["unit_price"],l["line_total"],bool(l.get("price_missing"))) for l in r["lines"]]:raise MigrationError("Позиции контракта не совпали")
            for r in records:
                if got[r["id"]].get("importFinancial")!=r["financial"]:raise MigrationError("Исходные суммы или замечания не сохранились")
            mapping,finance=finance_baseline(store,raw["finance"])
            vault=FileVault(store);docs=0;unlinked=[]
            for entry in manifest["files"]:
                name=entry["path"]
                if not name.startswith("linked-documents/"):continue
                parts=name.split("/",2)
                if len(parts)!=3:raise MigrationError("Неправильная ссылка документа")
                cid=parts[1]
                if cid not in ids:unlinked.append(name);continue
                with (preserved/name).open("rb") as stream:
                    info=vault.add_stream(uid("file-op",name),uid("file",name),parts[2],stream,entry["size"],cid,allow_relative=True)
                if info["sha256"]!=entry["sha256"]:raise MigrationError("Документ отличается от источника")
                docs+=1
            store.verify()
            report={"status":"TRIAL_BASELINE_PARTIAL","sourceManifestSha256":checksum(src/"manifest.json"),
                "activeContracts":len(records),"contractKopecks":sum(r["total"] for r in records if r["total"] is not None),
                "contractKopecksComplete":all(r["total"] is not None for r in records),
                "financialWarnings":[{"id":r["id"],"contractNumber":r["number"],**r["financial"]} for r in records if r["financial"]["issues"]],
                "missingPricePositions":[{"id":r["id"],"positions":r["missingPrices"]} for r in records if r["missingPrices"]],
                "derivedPricePositions":[{"id":r["id"],"positions":r["derivedPrices"]} for r in records if r["derivedPrices"]],
                "products":len(inv),"stockEventsPreserved":len(raw["stock"]["events"]),"documents":docs,
                "openingStockSource":"WORKBOOK_LEDGER_REPLAY" if manifest.get("origin")=="DRIVE_WORKBOOK_ONLY" else "EXCEL_API_CURRENT_BALANCES",
                "sourceOrigin":manifest.get("origin","LIVE_API_SNAPSHOT"),"missingSourceComponents":manifest.get("missingSourceComponents",[]),"openingStock":[{"product":k,"quantity":v["quantity"],"cents":v["cents"],"reserved":v["reserved"],"available":v["available"],"shortage":v["shortage"]} for k,v in inv.items()],
                "stockWarnings":stock_warnings,"reservationWarnings":reserve_warnings,"stockImportLocked":store.stock_import_locked(),
                "sourceDocuments":checks["documentFilesCopied"],"missingLinkedFolders":manifest.get("warnings",[]),
                "unlinkedDocumentsPreserved":unlinked,"historicalShippedWithoutEvent":legacy_shipped,
                "deletedProductsPreservedAsZeroStock":archived_deleted,"deletedExcelRowsPreserved":checks["deletedRowsInExcel"],
                "finance":finance,"expenseIdMapping":mapping,
                "checks":{"contractIdsFieldsItems":"PASS","sourceMoneyPreservation":"PASS","financialArithmetic":"WARN" if any(r["financial"]["issues"] for r in records) else "PASS","stockBalancesReservesCost":"PASS","stockHistoryReplay":"WARN" if stock_warnings else "PASS","reservationAllocation":"WARN" if reserve_warnings else "PASS","currentExpensesRates":"PASS","documentHashes":"PASS","sqliteIntegrity":"PASS"},
                "notVerified":["live Excel snapshot parity if source is a workbook-only backup","full Excel field/UI parity","monthly/annual financial and tax forecasts","historical stock UI and corrections","deleted contract normalization","drafts/notes/competitors UI","source COM snapshot transaction","real user installation"],
                "limitations":["Original historical stock events preserved, not re-applied; receipts history not normalized.",
                    "Current expenses imported as opening records; original financial history/revisions preserved in raw sources.",
                    "Unsupported Excel fields, deleted rows and drafts preserved in original workbook/JSON/raw staging, not editable in trial UI.",
                    "Missing document folders or unlinked archived documents may require manual remediation.",
                    "Do not use for production accounting; Excel remains authoritative."]}
            (attachments/REPORT).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            info=export_lab(store.path,attachments,archive,managed_dir=vault.root)
            if not info["valid"]:raise MigrationError("Архив не прошёл проверку")
            return report
        finally:store.close()

def prepare_from_installation(source,archive):
    with TemporaryDirectory(prefix="contracts-private-source-") as tmp:
        dest=Path(tmp)/"snapshot"
        capture_source(source,dest)
        return build_trial_archive(dest,archive)
