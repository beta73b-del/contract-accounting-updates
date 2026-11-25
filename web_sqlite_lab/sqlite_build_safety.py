"""Release gate for a WAL-safe SQLite runtime inside the actual EXE/.app.

SQLite WAL-reset corruption bug was fixed in 3.51.3, with documented
backports 3.44.6 / 3.50.7. The withdrawn 3.52.0 is rejected for release.
See official SQLite WAL documentation. This does not patch sqlite3 itself.
"""
from __future__ import annotations
import argparse
import json
from sqlite_runtime import sqlite3, SQLITE_REQUIRED_TEXT, require_engine

def patched_wal_reset(version=None):
    if version is None:version=sqlite3.sqlite_version_info
    version=tuple(int(n) for n in version[:3])
    return (version >= (3,53,0) or
            (3,51,3)<=version<(3,52,0) or
            (3,50,7)<=version<(3,51,0) or
            (3,44,6)<=version<(3,45,0))

def report():
    version=sqlite3.sqlite_version
    exact=version==SQLITE_REQUIRED_TEXT and require_engine()==SQLITE_REQUIRED_TEXT
    return {"sqliteVersion":version,
            "expectedVersion":SQLITE_REQUIRED_TEXT,
            "driver":"pysqlite3.dbapi2",
            "walResetFixed":patched_wal_reset(),
            "releaseReadyForWAL":bool(exact and patched_wal_reset()),
            "note":("Bundled verified SQLite 3.54.1" if exact and patched_wal_reset() else
                    "Runtime is not approved for release")}

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--require-safe",action="store_true",
                        help="Fail release if the packaged SQLite is unsafe for WAL")
    args=parser.parse_args(argv)
    result=report()
    print(json.dumps(result,sort_keys=True))
    return 0 if result["releaseReadyForWAL"] or not args.require_safe else 2

if __name__=="__main__":raise SystemExit(main())
