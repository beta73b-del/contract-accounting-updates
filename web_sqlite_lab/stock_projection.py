"""Pure reconstruction of the Excel Web Stock-Model event projection.

Separate from the active lab inventory tables; no production writes.
Preserves immutable source events. Edit/delete of a receipt supersedes the
original receipt rather than creating a second physical movement.

Source reference: Web server.ps1 Stock-Model and Stock-Cost, 25.11.2026.
The implementation is NOT approved as fully equivalent until private-data
comparison against Stock-Payload passes.
"""
from __future__ import annotations
from copy import deepcopy

DEFAULT_PRODUCTS=("Рутокен 1010","Рутокен 3.0 серт ФСТЭК","Рутокен 3.0 серт ФСБ")
MAX_QTY=1_000_000_000
MAX_CENTS=90_000_000_000_000

class StockError(ValueError):pass

def _long(x,name,allow_negative=False):
    if isinstance(x,bool) or not isinstance(x,int):
        raise StockError("Некорректное число: "+name)
    if abs(x)>MAX_CENTS or (not allow_negative and x<0):
        raise StockError("Количество или сумма вне допустимых пределов: "+name)
    return x

def weighted_cost(quantity,cents,withdrawal):
    _long(quantity,"physical");_long(cents,"cost");_long(withdrawal,"issue")
    if withdrawal<=0 or quantity<=0 or withdrawal>quantity:
        raise StockError("Недостаточное физическое количество")
    if withdrawal==quantity:return cents
    return (2*cents*withdrawal+quantity)//(2*quantity)

def _item(product):
    return {"product":product,"quantity":0,"cents":0,"reserved":0,"deleted":False}

def _needs(source):
    if not isinstance(source,list):raise StockError("Некорректные потребности")
    out=[]
    for n in source:
        if not isinstance(n,dict) or not isinstance(n.get("product"),str) or not n["product"]:
            raise StockError("Неизвестная позиция потребности")
        q=_long(n.get("quantity"),"needed quantity")
        if q<=0 or q>MAX_QTY:raise StockError("Потребность должна быть больше нуля")
        out.append({"product":n["product"],"quantity":q})
    return out

def project(events,default_products=DEFAULT_PRODUCTS):
    if not isinstance(events,list):raise StockError("Ожидается список складских событий")
    originals=deepcopy(events)
    fixes={}
    for e in originals:
        if not isinstance(e,dict) or not isinstance(e.get("kind"),str):
            raise StockError("Повреждено событие")
        if e["kind"] in ("editReceipt","deleteReceipt"):
            target=e.get("target")
            if not isinstance(target,str) or not target:
                raise StockError("Отсутствует ссылка на корректируемый приход")
            fixes[target]=e
    inventory={name:_item(name) for name in default_products}
    contracts={}
    ids=set()
    projected=[]
    for source in originals:
        eid=source.get("id")
        if not isinstance(eid,str) or not eid or eid in ids:
            raise StockError("Дублированный или отсутствующий идентификатор складской операции")
        ids.add(eid)
        e=deepcopy(source)
        kind=e["kind"]
        lines=e.get("lines",[])
        if not isinstance(lines,list):raise StockError("Неверные строки складского события")
        if kind=="receipt" and eid in fixes:
            correction=fixes[eid]
            if len(lines)!=1:raise StockError("Исправляемый приход должен иметь одну строку")
            lines[0]["quantity"]=_long(correction.get("correctedQuantity"),"corrected quantity")
            lines[0]["cents"]=_long(correction.get("correctedCents"),"corrected cost")
            if correction.get("correctedDate"):e["date"]=correction["correctedDate"]
            if correction["kind"]=="editReceipt":e["party"]=correction.get("party","")
            if correction["kind"]=="deleteReceipt":e["deleted"]=True
        for line in lines:
            if not isinstance(line,dict) or not isinstance(line.get("product"),str) or not line["product"]:
                raise StockError("Повреждена товарная строка")
            product=line["product"]
            item=inventory.setdefault(product,_item(product))
            if kind in ("createProduct","editProduct"):
                item["characteristics"]=e.get("characteristics") or ""
            if kind in ("createProduct","restoreProduct"):
                item["deleted"]=False
            elif kind=="deleteProduct":
                item["deleted"]=True
            q=_long(line.get("quantity"),"quantity",allow_negative=True)
            value=_long(line.get("cents"),"cents",allow_negative=True)
            if kind in ("editReceipt","deleteReceipt"):
                line["quantity"]=0
                line["cents"]=0
                continue
            if kind=="adjust" and e.get("targetQuantity") is not None:
                target_q=_long(e["targetQuantity"],"targetQuantity")
                target_cost=_long(e.get("targetCents"),"targetCents")
                if target_q==0 and target_cost:
                    raise StockError("Нулевой остаток не может иметь стоимость")
                q=target_q-item["quantity"]
                value=target_cost-item["cents"]
                line["quantity"]=q;line["cents"]=value
            elif kind=="ship" or (kind=="adjust" and q<0):
                if q>=0 or -q>item["quantity"]:
                    raise StockError("Отгрузка или списание привело к физическому дефициту")
                value=-weighted_cost(item["quantity"],item["cents"],-q)
                line["cents"]=value
            elif kind in ("return","cancel"):
                previous=contracts.get(e.get("contractId"))
                if not previous or previous["phase"]!="shipped" or not previous["shipment"]:
                    raise StockError("Нет отгрузки для возврата")
                matches=[x for x in previous["shipment"]["lines"] if x["product"]==product]
                if len(matches)!=1 or q!=-int(matches[0]["quantity"]):
                    raise StockError("Возврат не соответствует исходной отгрузке")
                value=-int(matches[0]["cents"])
                line["cents"]=value
            item["quantity"]+=q
            item["cents"]+=value
            if item["quantity"]<0 or item["cents"]<0 or (
                item["quantity"]==0 and item["cents"]!=0):
                raise StockError("Отрицательный остаток или некорректная оценка")
            if item["quantity"]>MAX_QTY or item["cents"]>MAX_CENTS:
                raise StockError("Превышен предел склада")
        cid=e.get("contractId")
        if cid:
            previous=contracts.get(cid)
            ship=e if kind=="ship" else (
                previous["shipment"] if e.get("phase")=="shipped" and previous else None)
            pending=(True if kind=="return" else False if kind in
                     ("ship","cancel","release") else
                     bool(previous and previous["returnedPending"]))
            contracts[cid]={"phase":e.get("phase"),"needs":_needs(e.get("needs",[])),
                            "shipment":ship,"returnedPending":pending}
        projected.append(e)
    for contract in contracts.values():
        if contract["phase"]!="reserved":continue
        for need in contract["needs"]:
            product=need["product"]
            inventory.setdefault(product,_item(product))["reserved"]+=need["quantity"]
    for item in inventory.values():
        if item["deleted"] and (item["quantity"]!=0 or item["reserved"]!=0):
            raise StockError("Удалён товар с остатком или резервом")
        item["available"]=item["quantity"]-item["reserved"]
        item["shortage"]=max(0,-item["available"])
    return {"inventory":inventory,"contracts":contracts,"events":projected}
