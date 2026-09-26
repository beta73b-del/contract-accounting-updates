# -*- coding: utf-8 -*-
"""
Чистые функции расчёта. Не зависят от tkinter/sqlite — легко тестировать отдельно.
"""
from datetime import date
import math

TAX_RATE = 0.07


def calc_tax(contract_sum: float) -> float:
    """Налог 7% от суммы контракта."""
    if not contract_sum:
        return 0.0
    return round(contract_sum * TAX_RATE, 2)


def calc_profit(contract_sum: float, purchase_cost: float, logistics: float,
                 commission: float, other_costs: float, guarantee: float, tax: float) -> float:
    """Чистая прибыль = сумма контракта минус все расходы (включая обеспечение/гарантию) и налог."""
    values = [contract_sum, purchase_cost, logistics, commission, other_costs, guarantee, tax]
    values = [v or 0.0 for v in values]
    contract_sum, purchase_cost, logistics, commission, other_costs, guarantee, tax = values
    return round(contract_sum - purchase_cost - logistics - commission - other_costs - guarantee - tax, 2)


def calc_margin_pct(profit: float, contract_sum: float):
    """Рентабельность = прибыль / сумма контракта. None, если сумма контракта пуста/0."""
    if not contract_sum:
        return None
    return profit / contract_sum


def days_until(deadline: date, today: date = None) -> int:
    """Сколько дней осталось до срока исполнения (может быть отрицательным, если срок прошёл)."""
    if today is None:
        today = date.today()
    return (deadline - today).days


def reminder_state(deadline: date, handover_date, today: date = None):
    """
    Возвращает состояние напоминания:
      ("done", None)      — контракт уже завершён (есть дата вручения)
      ("red", days)        — до срока <= 7 дней (включая просрочку, дни могут быть отрицательными)
      ("yellow", days)     — до срока 8..14 дней
      ("none", days)       — до срока больше 14 дней
      (None, None)         — срок исполнения не указан
    """
    if deadline is None:
        return None, None
    if handover_date:
        return "done", None
    days = days_until(deadline, today)
    if days <= 7:
        return "red", days
    if days <= 14:
        return "yellow", days
    return "none", days


def parse_money(text) -> float:
    """
    Парсит денежное/количественное значение из строки ввода (с пробелами/запятыми) в float.
    Пусто -> 0. Отрицательные числа, Infinity и NaN отклоняются как ValueError — в этом
    приложении суммы, себестоимость и количество товара не могут быть отрицательными
    или бесконечными (обнаружено при тестировании «грязного» пользовательского ввода).
    """
    if text is None:
        return 0.0
    text = str(text).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if text == "":
        return 0.0
    value = float(text)
    if math.isnan(value) or math.isinf(value):
        raise ValueError("значение должно быть обычным числом (не Infinity и не NaN)")
    if value < 0:
        raise ValueError("значение не может быть отрицательным")
    return value


def effective_exec_status(exec_status, deadline, handover_date, today: date = None) -> str:
    """
    «Просрочено» больше не хранится как ручной статус — вычисляется автоматически:
    если контракт не завершён (нет даты вручения) и срок исполнения уже прошёл, статус
    для отображения становится «Просрочено», независимо от того, что выбрано в поле.
    """
    if handover_date:
        return exec_status or "Исполнено"
    if deadline is None:
        return exec_status or ""
    if today is None:
        today = date.today()
    if deadline < today:
        return "Просрочено"
    return exec_status or ""


def payment_reminder_state(payment_deadline, payment_status: str, today: date = None):
    """
    Напоминание о крайнем сроке оплаты заказчиком:
      (None, None)        — срок оплаты не указан
      ("paid", None)       — уже оплачено (напоминание не нужно)
      ("overdue", days)    — срок прошёл, days — отрицательное число (на сколько дней просрочено)
      ("red", days)        — до срока осталось <= 3 дней
      ("yellow", days)     — до срока осталось 4..7 дней
      ("none", days)       — до срока больше 7 дней
    """
    if payment_deadline is None:
        return None, None
    if payment_status == "Оплачено":
        return "paid", None
    if today is None:
        today = date.today()
    days = (payment_deadline - today).days
    if days < 0:
        return "overdue", days
    if days <= 3:
        return "red", days
    if days <= 7:
        return "yellow", days
    return "none", days



def signing_reminder_state(sign_deadline, contract_status: str, today: date = None):
    """Состояние крайнего срока подписания.

    Рабочее окно напоминания — ровно 3, 2 и 1 день до крайней даты.
    В день крайнего срока и после него запись остаётся видимой как критическая/
    просроченная, но автоматическое e-mail напоминание отправляется только за
    3, 2 и 1 день (фильтр e-mail находится в main.py).
    """
    if sign_deadline is None:
        return None, None
    if contract_status == "Заключен":
        return "signed", None
    if today is None:
        today = date.today()
    days = (sign_deadline - today).days
    if days < 0:
        return "overdue", days
    if days in (0, 1, 2, 3):
        return "red", days
    return "none", days

def calc_contract_price(qty, cost, logistics, extra, commission, markup_pct, tax_pct):
    """
    Калькулятор цены для контракта — точный перенос формулы из исходного
    HTML-калькулятора. По себестоимости за единицу, расходам на партию (логистика,
    доп. расходы, комиссия площадки), желаемой наценке и налогу считает, по какой
    цене за штуку нужно продать, чтобы получить эту наценку после налога.

    Возвращает (цена_за_шт, сумма_контракта, чистая_прибыль).
    Если qty или cost равны 0 — считать нечего, возвращает (None, None, None).
    """
    if not qty or not cost:
        return None, None, None
    if tax_pct >= 100:
        raise ValueError("Налог не может быть 100% или больше — на такую ставку цену не посчитать.")
    price = (cost + logistics / qty + extra / qty + commission / qty
             + cost * (markup_pct / 100)) / (1 - tax_pct / 100)
    total_sum = price * qty
    profit = total_sum - cost * qty - logistics - extra - commission - total_sum * (tax_pct / 100)
    return price, total_sum, profit