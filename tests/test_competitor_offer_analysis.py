"""Каждая победа конкурента — отдельное наблюдение количества и цены."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db


def _record(conn, name, inn, product, qty, price, day):
    return db.insert_competitor_record(conn, {
        "competitor": name, "competitor_inn": inn, "product": product,
        "trade_type": "Электронный аукцион", "qty": qty,
        "unit_price": price, "purchase_date": day,
    })


def test_offers_keep_quantities_separate_and_calculate_price_changes():
    conn = db.get_connection(":memory:")
    _record(conn, "Конкурент А", "1234567890", "Рутокен Lite 1010", 150, 1250, "2026-09-01")
    _record(conn, "ООО Конкурент А", "1234567890", "Рутокен Lite 1010", 250, 1250, "2026-09-15")
    _record(conn, "Конкурент Б", "9876543210", "Рутокен Lite 1010", 600, 900, "2026-09-16")
    _record(conn, "Конкурент А", "1234567890", "Рутокен ЭЦП 3.0 3120", 50, 3200, "2026-09-17")
    _record(conn, "Конкурент А", "1234567890", "Рутокен Lite 1010", 300, 1200, "2026-09-18")

    offers = db.competitor_offer_analysis(conn)
    assert len(offers) == 5
    company = sorted(
        (r for r in offers if r["competitor_inn"] == "1234567890"
         and r["product"] == "Рутокен Lite 1010"),
        key=lambda r: r["qty"],
    )
    assert [r["qty"] for r in company] == [150, 250, 300]
    assert [r["unit_price"] for r in company] == [1250, 1250, 1200]
    assert company[0]["qty_change"] is None
    assert company[1]["qty_change"] == 100
    assert company[1]["price_change_pct"] == 0
    assert company[2]["qty_change"] == 50
    assert abs(company[2]["price_change_pct"] + 0.04) < 1e-10
    other = next(r for r in offers if r["competitor_inn"] == "9876543210")
    assert other["price_change_pct"] is None
    other_product = next(r for r in offers if r["product"] == "Рутокен ЭЦП 3.0 3120")
    assert other_product["price_change_pct"] is None
    assert len(db.fetch_competitor_records(conn)) == 5
    # Неполная историческая строка остаётся видимой, не участвуя в сравнении.
    _record(conn, "Конкурент А", "1234567890", "Рутокен Lite 1010", None, None, "2026-09-19")
    offers = db.competitor_offer_analysis(conn)
    assert len(offers) == 6
    incomplete = next(r for r in offers if r["purchase_date"] == "2026-09-19")
    assert incomplete["qty_change"] is None
    assert incomplete["price_change_pct"] is None
    conn.close()


if __name__ == "__main__":
    test_offers_keep_quantities_separate_and_calculate_price_changes()
    print("PASS: competitor offer observations and per-product history")
