"""Cross-platform transfer CLI for a fresh installation ONLY.

Export performs an online SQLite snapshot and verifies every document SHA.
Restore creates a NEW nonexisting data directory containing lab.db and both
active and recoverable trashed files. Never overwrites a live installation.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from transfer_package import export_lab,inspect_transfer,restore_new_install

def run(argv=None):
    parser=argparse.ArgumentParser(description="Контракты ИП SQLite LAB: безопасный перенос")
    subs=parser.add_subparsers(dest="command",required=True)
    export=subs.add_parser("export",help="Создать проверенный ZIP")
    export.add_argument("--data-root",required=True,help="Каталог с lab.db")
    export.add_argument("--output",required=True,help="НОВЫЙ файл резервной копии")
    verify=subs.add_parser("verify",help="Проверить ZIP до восстановления")
    verify.add_argument("--archive",required=True)
    restore=subs.add_parser("restore",help="Восстановить только в новый каталог")
    restore.add_argument("--archive",required=True)
    restore.add_argument("--new-data-root",required=True)
    a=parser.parse_args(argv)
    if a.command=="export":
        root=Path(a.data_root).resolve()
        if not (root/"lab.db").is_file():
            parser.error("В указанном каталоге отсутствует lab.db")
        docs=root/"attachments"
        if not docs.is_dir():
            parser.error("Отсутствует каталог attachments; экспорт остановлен без потери данных")
        result=export_lab(root/"lab.db",docs,a.output,managed_dir=root/"managed_files")
    elif a.command=="verify":
        result=inspect_transfer(a.archive)
    else:
        result=restore_new_install(a.archive,a.new_data_root)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(run())
