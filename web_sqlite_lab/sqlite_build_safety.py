"""Release gate for a WAL-safe SQLite runtime inside the actual EXE/.app.

SQLite WAL-reset corruption bug was fixed in 3.51.3, with documented
backports 3.44.6 / 3.50.7. The withdrawn 3.52.0 is rejected for release.
See official SQLite WAL documentation. This does not patch sqlite3 itself.
"""
from __future__ import annotations
import argparse
import json
import sqlite3

def patched_wal_reset(version=None):
    if version is None:version=sqlite3.sqlite_version_info
    version=tuple(int(n) for n in version[:3])
    return (version >= (3,53,0) or
            (3,51,3)<=version<(3,52,0) or
            (3,50,7)<=version<(3,51,0) or
            (3,44,6)<=version<(3,45,0))

def report():
    version=sqlite3.sqlite_version
    return {"sqliteVersion":version,
            "walResetFixed":patched_wal_reset(),
            "releaseReadyForWAL":patched_wal_reset(),
            "note":("Patched runtime" if patched_wal_reset() else
                    "Unpatched WAL-reset risk; production release blocked")}

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--require-safe",action="store_true",
                        help="Fail release if the packaged SQLite is unsafe for WAL")
    args=parser.parse_args(argv)
    result=report()
    print(json.dumps(result,sort_keys=True))
    return 0 if result["releaseReadyForWAL"] or not args.require_safe else 2

if __name__=="__main__":raise SystemExit(main())
