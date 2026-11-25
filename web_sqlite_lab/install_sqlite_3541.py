"""Install pinned SQLite 3.54.1 driver for QA Windows/macOS/Linux.

For macOS Intel, PyPI has no pysqlite3 cp311 x86_64 binary wheel.
Build it from the verified pysqlite3 0.6.2 sdist plus official SQLite
amalgamation 3.54.1 and check its published SHA3-256.

The build runs entirely on a CI runner, never touches user databases.
"""
from __future__ import annotations
from hashlib import sha256, sha3_256
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
from urllib.request import urlopen
import os
import platform
import sys
import tarfile
import zipfile

PACKAGE = "pysqlite3==0.6.2"
SQLITE = "3.54.1"
SOURCE_URL = "https://www.sqlite.org/2026/sqlite-amalgamation-3540100.zip"
SOURCE_SHA3 = "92281c86e2957f9b9305890f9ac04204caa7b677cc868dc326d942689df28e30"
PYPI_SOURCE_SHA256 = "67b4a2c3e0f3b780cc8e5ac0efdc55a2bb99a63027070b4541eb8acd3dfb55ff"

def cmd(*argv):
    run([sys.executable, "-m", *argv], check=True)

def install():
    # Wheels exist for Win x64, Mac ARM64, Linux x64; force them rather than
    # silently compiling against an old OS-supplied libsqlite3.
    intel = sys.platform == "darwin" and platform.machine() == "x86_64"
    if not intel:
        cmd("pip", "install", "--only-binary=:all:", PACKAGE)
    else:
        with TemporaryDirectory(prefix="sqlite-3541-intel-") as tmp:
            root = Path(tmp)
            cmd("pip", "download", "--no-deps", "--no-binary=:all:",
                "--dest", str(root), PACKAGE)
            source_archives = list(root.glob("pysqlite3-0.6.2.tar.gz"))
            if len(source_archives) != 1:
                raise RuntimeError("Required pysqlite3 0.6.2 sdist unavailable")
            data = source_archives[0].read_bytes()
            if sha256(data).hexdigest() != PYPI_SOURCE_SHA256:
                raise RuntimeError("pysqlite3 source SHA-256 differs from PyPI")
            with tarfile.open(source_archives[0], "r:gz") as archive:
                members = archive.getmembers()
                if any(not m.name.startswith("pysqlite3-0.6.2/") or
                       m.issym() or m.islnk() or
                       ".." in Path(m.name).parts for m in members):
                    raise RuntimeError("Unsafe Python source archive")
                archive.extractall(root)
            tree = root / "pysqlite3-0.6.2"
            with urlopen(SOURCE_URL, timeout=45) as response:
                amalgamation = response.read(6_000_000)
            if sha3_256(amalgamation).hexdigest() != SOURCE_SHA3:
                raise RuntimeError("SQLite 3.54.1 SHA3-256 differs from official site")
            zipped = root / "official_sqlite.zip"
            zipped.write_bytes(amalgamation)
            with zipfile.ZipFile(zipped) as archive:
                for name in ("sqlite3.c", "sqlite3.h"):
                    matches = [p for p in archive.namelist() if p.endswith("/" + name)]
                    if len(matches) != 1:
                        raise RuntimeError("Missing SQLite amalgamation " + name)
                    (tree / name).write_bytes(archive.read(matches[0]))
            # pysqlite3 detects sqlite3.c/sqlite3.h at the project root and
            # statically compiles SQLite into its CPython extension.
            cmd("pip", "install", "--no-build-isolation", "--no-deps", str(tree))
    # Must inspect the loaded engine, not Python stdlib's sqlite3 version.
    from sqlite_runtime import require_engine
    print("Verified bundled SQLite", require_engine(), flush=True)

if __name__ == "__main__":
    install()
