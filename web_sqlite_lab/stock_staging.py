"""R2/R3: create a NEW read-only-derived SQLite stock-event staging database.

The original R2 SQLite staging and the real Excel installation are never
modified. Event replay must match the source API stock balances and phases;
otherwise import is rejected with no destination file.

This staging schema is *not* the operational SQLite application's schema.
"""
from __future__ import annotations
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import uuid

from stock_projection import project, StockError


class StockStageError(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _source_connection(path):
    return sqlite3.connect(path.as_uri()+"?mode=ro", uri=True)


def _validate_stock(stock):
    if not isinstance(stock, dict):
        raise StockStageError("Missing stock section")
    events, items, phases = stock.get("events"), stock.get("items"), stock.get("phases")
    if not isinstance(events, list) or not isinstance(items, list) or not isinstance(phases, dict):
        raise StockStageError("Incomplete stock source")
    found = set()
    for e in events:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not e["id"]:
            raise StockStageError("Invalid event ID")
        if e["id"] in found:
            raise StockStageError("Duplicate stock event ID")
        if e.get("kind") in ("editReceipt", "deleteReceipt"):
            source = next((previous for previous in events if previous.get("id") == e.get("target")), None)
            if source is None or source.get("kind") != "receipt":
                raise StockStageError("Correction refers to missing receipt")
        found.add(e["id"])
    reference = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("product"), str):
            raise StockStageError("Invalid inventory item")
        if item["product"] in reference:
            raise StockStageError("Duplicate product in source stock")
        reference[item["product"]] = item
    try:
        projection = project(events)
    except StockError as exc:
        raise StockStageError("Source ledger cannot be replayed") from exc
    actual = projection["inventory"]
    if set(reference) != set(actual):
        raise StockStageError("Stock product set mismatch")
    fields = ("quantity", "cents", "reserved", "available", "shortage", "deleted")
    for product, expected in reference.items():
        computed = actual[product]
        for field in fields:
            if type(expected.get(field)) is not type(computed.get(field)) or expected.get(field) != computed.get(field):
                raise StockStageError("Stock valuation or reserve mismatch")
    computed_phases = {cid: data["phase"] for cid, data in projection["contracts"].items()}
    if computed_phases != phases:
        raise StockStageError("Contract stock-phase mismatch")
    return projection


def build_stock_staging(source_db, destination_db):
    """Clone raw staging and atomically add stock events + checked projection."""
    src = Path(source_db).resolve(strict=True)
    dst = Path(destination_db).resolve()
    if src == dst or dst.exists():
        raise FileExistsError("Source and target must be distinct; existing data never overwritten")
    if not src.is_file() or not dst.parent.is_dir():
        raise StockStageError("Source or destination parent is unavailable")
    with closing(_source_connection(src)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise StockStageError("Source SQLite is damaged")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise StockStageError("Source SQLite FK mismatch")
        table = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_sections'").fetchone()
        if not table:
            raise StockStageError("Source does not contain raw API sections")
        row = con.execute("SELECT payload_json FROM raw_sections WHERE name='stock'").fetchone()
        if not row:
            raise StockStageError("Stock section not captured")
        stock = json.loads(row[0])
        projection = _validate_stock(stock)
        event_count = len(stock["events"])
        product_count = len(projection["inventory"])
        temp = dst.with_name(dst.name + ".staging-" + uuid.uuid4().hex)
        try:
            with closing(sqlite3.connect(temp)) as staging:
                con.backup(staging)
                staging.execute("PRAGMA foreign_keys=ON")
                staging.execute("PRAGMA journal_mode=DELETE")
                staging.executescript("""
                        CREATE TABLE stock_event_archive(
                            seq INTEGER PRIMARY KEY,
                            event_id TEXT NOT NULL UNIQUE,
                            kind TEXT NOT NULL,
                            payload_json TEXT NOT NULL,
                            sha256 TEXT NOT NULL
                        );
                        CREATE TABLE stock_projected_item(
                            product TEXT PRIMARY KEY,
                            physical_qty INTEGER NOT NULL CHECK(physical_qty>=0),
                            inventory_cost_kopecks INTEGER NOT NULL CHECK(inventory_cost_kopecks>=0),
                            reserved_qty INTEGER NOT NULL CHECK(reserved_qty>=0),
                            available_qty INTEGER NOT NULL,
                            shortage_qty INTEGER NOT NULL CHECK(shortage_qty>=0),
                            deleted INTEGER NOT NULL CHECK(deleted IN (0,1))
                        );
                        CREATE TABLE stock_projected_phase(
                            contract_id TEXT PRIMARY KEY,
                            phase TEXT
                        );
                    """)
                staging.execute("BEGIN IMMEDIATE")
                try:
                    for sequence, event in enumerate(stock["events"], 1):
                        payload = _json(event)
                        staging.execute("INSERT INTO stock_event_archive VALUES (?,?,?,?,?)",
                                        (sequence, event["id"], event["kind"], payload,
                                         sha256(payload.encode("utf-8")).hexdigest()))
                    for product, item in projection["inventory"].items():
                        staging.execute("INSERT INTO stock_projected_item VALUES (?,?,?,?,?,?,?)",
                                        (product, item["quantity"], item["cents"], item["reserved"],
                                         item["available"], item["shortage"], int(item["deleted"])))
                    for cid, data in projection["contracts"].items():
                        staging.execute("INSERT INTO stock_projected_phase VALUES (?,?)",
                                        (cid, data["phase"]))
                    staging.execute("COMMIT")
                except BaseException:
                    if staging.in_transaction:
                        staging.execute("ROLLBACK")
                    raise
                if staging.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise StockStageError("Destination SQLite integrity error")
                if staging.execute("PRAGMA foreign_key_check").fetchall():
                    raise StockStageError("Destination SQLite FK error")
                if staging.execute("SELECT COUNT(*) FROM stock_event_archive").fetchone()[0] != event_count:
                    raise StockStageError("Stock event import is incomplete")
            # Close file handles BEFORE atomic rename, including on Windows.
            if dst.exists():
                raise FileExistsError("Destination appeared during import")
            temp.rename(dst)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    return {"status": "STOCK_RAW_STAGING_ONLY", "events": event_count,
            "products": product_count, "parity": "PASS",
            "sourceUnmodified": True,
            "activeInventoryUpdated": False}

if __name__=="__main__":
    import sys
    if len(sys.argv)!=3:
        raise SystemExit("Usage: python stock_staging.py <read-only-raw-staging.sqlite> <NEW-staging.sqlite>")
    print(json.dumps(build_stock_staging(sys.argv[1],sys.argv[2]),ensure_ascii=False))
