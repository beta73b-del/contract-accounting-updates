"""Single DB-API driver for all SQLite LAB application databases.

SQLite 3.54.1 is bundled with pysqlite3 0.6.2. Do not fall back to the
CPython/system sqlite3 engine (3.45.1 in previous Windows/macOS QA builds).
The exact version gate prevents opening a user's database with an unexpected
SQLite binary, including after PyInstaller packaging.
"""
from pysqlite3 import dbapi2 as sqlite3

SQLITE_REQUIRED_VERSION = (3, 54, 1)
SQLITE_REQUIRED_TEXT = "3.54.1"

def engine_version():
    with sqlite3.connect(":memory:") as db:
        return db.execute("SELECT sqlite_version()").fetchone()[0]

def require_engine():
    actual = engine_version()
    if actual != SQLITE_REQUIRED_TEXT or sqlite3.sqlite_version != SQLITE_REQUIRED_TEXT:
        raise RuntimeError(
            "SQLite runtime mismatch: required " + SQLITE_REQUIRED_TEXT +
            ", found SQL engine=" + actual +
            ", module=" + sqlite3.sqlite_version +
            ". Installation/build aborted; user data was not opened."
        )
    return actual

require_engine()
