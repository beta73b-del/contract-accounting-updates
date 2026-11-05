# -*- coding: utf-8 -*-
"""Read-only price board and competitor drill-down for the two Rutoken models.

The source is the existing competitor_records table. These are recorded purchase
observations, not a live EIS feed and not necessarily every submitted bid.
No schema change or implicit database writes are performed.
"""
from collections import Counter
from datetime import datetime
import math
import statistics

import db

PRODUCTS = db.FIXED_PRODUCTS
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y")


def _date_key(value):
    raw = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def sort_offers(offers):
    """Newest recorded date first; undated entries last. Ties use record id."""
    return sorted(offers, key=lambda row: (_date_key(row.get("purchase_date")),
                                          int(row.get("id") or 0)), reverse=True)


def price_statistics(offers):
    """One observation = one price; do not weight prices by procurement quantity."""
    prices = []
    for item in offers:
        try:
            value = float(item.get("unit_price"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            prices.append(value)
    return {
        "observations": len(offers),
        "priced": len(prices),
        "minimum": min(prices) if prices else None,
        "maximum": max(prices) if prices else None,
        "average": statistics.mean(prices) if prices else None,
        "median": statistics.median(prices) if prices else None,
    }


def product_price_board(conn):
    """Two always-present product sections, populated from historical observations."""
    board = {name: [] for name in PRODUCTS}
    for record in db.fetch_competitor_records(conn):
        item = dict(record)
        canonical = db.canonical_product_name(item.get("product"))
        if canonical in board:
            item["product"] = canonical
            board[canonical].append(item)
    return {product: sort_offers(rows) for product, rows in board.items()}


def competitor_identity(offer):
    """Prefer INN. Different INNs must never be merged by similar legal names."""
    inn = db.normalize_inn(offer.get("competitor_inn"))
    if inn:
        return "inn:" + inn
    normalized = db.normalize_competitor_name(offer.get("competitor"))
    return "name:" + normalized if normalized else None


def competitor_profiles(board):
    """Separate historical price distributions by model and trading platform.

    Profile values are computed from the two product boards, not from sales
    totals: they describe recorded competitor purchases only.
    """
    grouped = {}
    for model in PRODUCTS:
        for row in board.get(model, []):
            key = competitor_identity(row)
            if key:
                grouped.setdefault(key, []).append(row)

    profiles = []
    for key, records in grouped.items():
        sorted_records = sort_offers(records)
        labels = Counter(str(r.get("competitor") or "").strip() for r in records
                         if str(r.get("competitor") or "").strip())
        display_name = labels.most_common(1)[0][0] if labels else "Неизвестный конкурент"
        inn = key[4:] if key.startswith("inn:") else ""
        product_stats = []
        platform_groups = {}
        for model in PRODUCTS:
            product_rows = [r for r in sorted_records if r["product"] == model]
            if product_rows:
                product_stats.append({"product": model, **price_statistics(product_rows)})
            for row in product_rows:
                original = str(row.get("platform") or "").strip()
                label = original or "Не указана"
                platform_key = (model, label.casefold())
                group = platform_groups.setdefault(
                    platform_key, {"product": model, "platform": label, "rows": []})
                group["rows"].append(row)

        platforms = []
        for group in platform_groups.values():
            rows = sort_offers(group["rows"])
            platforms.append({
                "product": group["product"],
                "platform": group["platform"],
                "last_date": rows[0].get("purchase_date") or "",
                **price_statistics(rows),
            })
        platforms.sort(key=lambda x: (PRODUCTS.index(x["product"]),
                                     x["platform"].casefold()))
        profiles.append({
            "key": key,
            "competitor": display_name,
            "competitor_inn": inn,
            "records": sorted_records,
            "observations": len(sorted_records),
            "last_date": sorted_records[0].get("purchase_date") or "",
            "products": product_stats,
            "platforms": platforms,
        })
    return sorted(profiles, key=lambda x: (x["competitor"].casefold(),
                                          x["competitor_inn"]))
