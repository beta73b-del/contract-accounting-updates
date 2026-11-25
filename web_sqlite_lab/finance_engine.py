"""Pure financial parity of current Web app/logic.mjs calculateSummary.

All values are integer kopecks. This is the management FORECAST, *not*
final Russian income tax accounting or the annual 1% minimum reconciliation.
It is intentionally independent of stock costing and sourced from records'
existing cost fields (as in the live Web UI).
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal,InvalidOperation
import re

LIMIT=90_000_000_000_000
FIELDS=("count","contract","paid","expected","cost","fee","logistics","other",
        "common","expenses","profitBefore","manualTax","autoTaxProfit",
        "tax","operatingExpenses","profitAfter")

class FinanceError(ValueError):pass

def kopecks(value,allow_empty=False):
    if value is None and allow_empty:return 0
    if isinstance(value,bool) or isinstance(value,float):
        raise FinanceError("Money must be a precise decimal or string")
    text=str(value if value is not None else "").strip()
    text=re.sub(r"[\s\u00a0\u202f₽]","",text).replace(",",".")
    if text=="" and allow_empty:return 0
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?",text):raise FinanceError("Invalid money")
    try:result=int(Decimal(text)*100)
    except (InvalidOperation,ValueError):raise FinanceError("Invalid money") from None
    if not (0<=result<=LIMIT):raise FinanceError("Amount out of bounds")
    return result

def _annual_bp(rates,year):
    if year==2026:return 500
    raw=rates.get(str(year),rates.get(year))
    return raw*100 if type(raw)==int and raw in (5,15) else None

def _int_year_month(value):
    # JSX logic uses Number(value), but dates/accounting must not silently coerce booleans.
    if isinstance(value,bool):return None
    try:
        if not re.fullmatch(r"\d{1,4}",str(value)):return None
        return int(value)
    except (TypeError,ValueError):return None

def _active(record):
    if not isinstance(record,dict) or record.get("deleted") is True:return False
    return str(record.get("check","")).strip().casefold()!="удалён" and str(record.get("status","")).strip().casefold()!="удалён"

def _month():
    return {"month":0,"count":0,"contract":0,"paid":0,"expected":0,"cost":0,
            "fee":0,"logistics":0,"other":0,"common":0,"expenses":0,
            "profitBefore":0,"tax":0,"manualTax":0,"autoTaxProfit":0,
            "operatingExpenses":0,"profitAfter":0}

def forecast(records,year,finance=None):
    """Return same result keys/kopeck integers as app/logic.mjs calculateFinancialSummary."""
    if type(year)!=int or year<2026 or year>2100:raise FinanceError("Invalid year")
    finance=finance or {}
    rate=_annual_bp(finance.get("rates") or {},year)
    months=[dict(_month(),month=m) for m in range(1,13)]
    for r in records:
        if not _active(r) or _int_year_month(r.get("year"))!=year or r.get("check")!="OK":
            continue
        month=_int_year_month(r.get("month"))
        if month is None or not 1<=month<=12:continue
        try:
            amounts={k:kopecks(r.get(k),allow_empty=(k!="amount"))
                     for k in ("amount","cost","fee","logistics","other")}
            explicit=str(r.get("taxAmount") if r.get("taxAmount") is not None else "").strip()
            manual=kopecks(explicit) if explicit else None
        except FinanceError:
            # The source logic intentionally ignores malformed rows in this summary.
            continue
        m=months[month-1]
        m["count"]+=1
        m["contract"]+=amounts["amount"]
        for k in ("cost","fee","logistics","other"):m[k]+=amounts[k]
        if manual is None:
            m["autoTaxProfit"]+=(amounts["amount"]-amounts["cost"]-amounts["fee"]
                                -amounts["logistics"]-amounts["other"])
        else:m["manualTax"]+=manual
        if str(r.get("status","")).casefold()=="оплачен":m["paid"]+=amounts["amount"]
        else:m["expected"]+=amounts["amount"]
    for item in finance.get("expenses",[]) or []:
        date_string=item.get("date","")
        if not isinstance(date_string,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date_string):
            continue
        if int(date_string[:4])!=year:continue
        month=int(date_string[5:7])
        if not 1<=month<=12:continue
        expense=kopecks(item.get("amount"))
        if expense<=0:raise FinanceError("Invalid shared expense")
        months[month-1]["common"]+=expense
    for m in months:
        m["expenses"]=m["cost"]+m["fee"]+m["logistics"]+m["other"]+m["common"]
        m["profitBefore"]=m["contract"]-m["expenses"]
        profit=max(0,m["autoTaxProfit"]-m["common"])
        # JS Math.round for nonnegative operands -> half up. No float tax drift.
        m["tax"]=None if rate is None else m["manualTax"]+(profit*rate*2+10000)//20000
        m["operatingExpenses"]=None if rate is None else m["fee"]+m["logistics"]+m["other"]+m["tax"]
        m["profitAfter"]=None if rate is None else m["contract"]-m["cost"]-m["operatingExpenses"]-m["common"]
    total=_month()
    for m in months:
        for k in FIELDS:
            if k in ("tax","operatingExpenses","profitAfter") and rate is None:continue
            total[k]+=m[k]
    if rate is None:
        total["tax"]=None;total["operatingExpenses"]=None;total["profitAfter"]=None
    return {"year":year,"months":months,"total":total,"monthlyEstimatedTax":total["tax"]}

def record_period_summary(records, year, finance=None):
    return forecast(records,year,finance)

def validate_finance_expense_date(value):
    if not isinstance(value,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value):
        raise FinanceError("Expense date must be YYYY-MM-DD")
    try:d=date.fromisoformat(value)
    except ValueError:raise FinanceError("Invalid expense date") from None
    if not (date(2026,11,1)<=d<=date(2100,12,31)):
        raise FinanceError("Expense outside supported period")
    return value
