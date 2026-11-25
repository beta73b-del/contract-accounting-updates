"""Import the validated current Web stock snapshot, never recompute opening balances.

Historical projection is an independent audit. A discrepancy is not evidence
that the source balance is false. Unresolved reservation allocation locks stock
mutations until a later explicit reconciliation; raw journal remains unchanged.
"""
from stock_staging import _validate_stock
from stock_projection import project,MAX_QTY,MAX_CENTS
from collections import defaultdict

class BaselineError(ValueError):pass

def integer(value,label,lo,hi):
    if type(value) is not int or not lo<=value<=hi:
        raise BaselineError("Некорректное значение склада: "+label)
    return value

def baseline(stock):
    if not isinstance(stock,dict):raise BaselineError("Нет исходного склада")
    items,events,phases=stock.get("items"),stock.get("events"),stock.get("phases")
    if not isinstance(items,list) or not isinstance(events,list) or not isinstance(phases,dict):
        raise BaselineError("Неполный снимок склада Excel")
    inv={}
    for x in items:
        if not isinstance(x,dict):raise BaselineError("Повреждена строка склада")
        name=x.get("product")
        if not isinstance(name,str) or not name.strip() or len(name)>200 or name in inv:
            raise BaselineError("Неверное или повторное наименование товара")
        q=integer(x.get("quantity"),name+" физический остаток",0,MAX_QTY)
        c=integer(x.get("cents"),name+" стоимость",0,MAX_CENTS)
        r=integer(x.get("reserved"),name+" резерв",0,MAX_QTY)
        d=x.get("deleted",False)
        if type(d) is not bool or (d and (q or c or r)) or (q==0 and c):
            raise BaselineError("Внутренне противоречивый исходный остаток: "+name)
        a=q-r;s=max(0,-a)
        for field,value in (("available",a),("shortage",s)):
            if field in x and (type(x[field]) is not int or x[field]!=value):
                raise BaselineError("Исходный Excel: противоречие "+field+" по товару "+name)
        inv[name]={**x,"quantity":q,"cents":c,"reserved":r,"available":a,"shortage":s,"deleted":d}
    if any(not isinstance(k,str) or (v is not None and not isinstance(v,str)) for k,v in phases.items()):
        raise BaselineError("Некорректный справочник фаз Excel")
    warnings=[];replay=None;candidate=None
    try:
        replay=_validate_stock(stock)
        candidate=replay
    except (ValueError,TypeError,KeyError) as e:
        warnings.append({"kind":"journalParity","message":str(e)})
        # Produce explicit per-product differences if independent replay is possible.
        # Do not use the result to seed inventory or to synthesize a stock movement.
        try:
            candidate=project(events)
            for name in sorted(set(inv)|set(candidate["inventory"])):
                expected=inv.get(name);actual=candidate["inventory"].get(name)
                if expected is None or actual is None:
                    warnings.append({"kind":"journalProduct","product":name,"sourcePresent":expected is not None,"replayPresent":actual is not None})
                    continue
                for field in ("quantity","cents","reserved","available","shortage","deleted"):
                    if actual.get(field)!=expected[field]:
                        warnings.append({"kind":"journalDifference","product":name,"field":field,"excel":expected[field],"replay":actual.get(field)})
        except (ValueError,TypeError,KeyError):pass
    return {"inventory":inv,"phases":phases,"verifiedReplay":replay,"reservationReplay":candidate,"warnings":warnings}

def reservation_differences(inv,phases,records,replay):
    """Check attribution, not merely the sum, before allowing stock mutations."""
    warnings=[];totals=defaultdict(int);active_ids={r["id"] for r in records}
    for r in records:
        cid=r["id"];phase=phases.get(cid);reserved=r["status"]=="заключен"
        if (phase=="reserved")!=reserved:
            warnings.append({"kind":"reservationPhase","id":cid,"excelPhase":phase,"sqliteStatus":r["status"]})
        if phase=="shipped" and r["status"] not in ("Отправлен","Вручен","Оплачен"):
            warnings.append({"kind":"shipmentPhase","id":cid,"excelPhase":phase,"sqliteStatus":r["status"]})
        if reserved:
            qty=defaultdict(int)
            for line in r["lines"]:qty[line["product"]]+=line["quantity"]
            for name,q in qty.items():totals[name]+=q
            if replay is not None:
                needs=defaultdict(int)
                for n in replay["contracts"].get(cid,{}).get("needs",[]):needs[n["product"]]+=n["quantity"]
                if dict(qty)!=dict(needs):
                    warnings.append({"kind":"reservationAllocation","id":cid,"excelNeeds":dict(needs),"sqliteNeeds":dict(qty)})
    if replay is not None:
        candidate_phases={cid:v.get("phase") for cid,v in replay["contracts"].items() if v.get("phase")=="reserved"}
        source_phases={cid:phase for cid,phase in phases.items() if phase=="reserved"}
        if candidate_phases!=source_phases:warnings.append({"kind":"reservationJournalPhaseMismatch"})
    for cid,phase in phases.items():
        if phase=="reserved" and cid not in active_ids:
            warnings.append({"kind":"reservationMissingContract","id":cid})
    for name,item in inv.items():
        if totals[name]!=item["reserved"]:
            warnings.append({"kind":"reservationTotal","product":name,"excel":item["reserved"],"sqlite":totals[name]})
    # Without a verified historical replay, the per-contract attribution is
    # unproven if any reserve remains; keep the imported stock read-only.
    if replay is None and any(x["reserved"] for x in inv.values()):
        warnings.append({"kind":"reservationAttributionNotVerified"})
    return warnings
