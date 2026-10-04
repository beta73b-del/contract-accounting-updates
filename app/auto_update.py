# -*- coding: utf-8 -*-
"""Тихое обновление UchetZakupok через публичные GitHub Releases.

Релиз должен содержать два assets:
- UchetZakupok.exe
- UchetZakupok.exe.sha256  (64-символьный SHA-256, допускается формат sha256sum)

Пользовательские data/ и локальная рабочая SQLite не затрагиваются: заменяется
только исполняемый файл приложения.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app_version import __version__

GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
DEFAULT_EXE_ASSET = "UchetZakupok.exe"
SHA_SUFFIX = ".sha256"
USER_AGENT = f"UchetZakupok/{__version__}"


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    exe_url: str
    sha_url: str
    notes: str = ""


def normalize_repo(value: str) -> str:
    """Принимает owner/repo или обычный URL github.com/owner/repo."""
    value = (value or "").strip()
    if not value:
        return ""
    value = re.sub(r"^https?://(?:www\.)?github\.com/", "", value, flags=re.I)
    value = value.strip().strip("/")
    if value.lower().endswith(".git"):
        value = value[:-4]
    # Убираем случайно вставленные хвосты releases/tag/... и т.п.
    parts = [p for p in value.split("/") if p]
    if len(parts) < 2:
        return ""
    owner, repo = parts[0], parts[1]
    valid = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not valid.match(owner) or not valid.match(repo):
        return ""
    return f"{owner}/{repo}"


def _version_tuple(value: str):
    value = (value or "").strip().lstrip("vV")
    nums = []
    for part in value.split("."):
        m = re.match(r"(\d+)", part)
        nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums[:4])


def is_newer(remote: str, current: str = __version__) -> bool:
    return _version_tuple(remote) > _version_tuple(current)


def _json_get(url: str, timeout: float = 5.0):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_latest(repo: str, timeout: float = 5.0, exe_asset: str = DEFAULT_EXE_ASSET):
    repo = normalize_repo(repo)
    if not repo:
        return None
    try:
        data = _json_get(GITHUB_API.format(repo=repo), timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Не удалось проверить GitHub Releases: {exc}") from exc

    tag = str(data.get("tag_name") or "").strip()
    remote_version = tag.lstrip("vV")
    if not tag or not is_newer(remote_version):
        return None

    assets = {str(a.get("name") or ""): a for a in (data.get("assets") or [])}
    exe = assets.get(exe_asset)
    sha = assets.get(exe_asset + SHA_SUFFIX)
    if not exe or not sha:
        raise UpdateError(
            f"Релиз {tag} найден, но в нём нет {exe_asset} и/или {exe_asset + SHA_SUFFIX}."
        )
    exe_url = str(exe.get("browser_download_url") or "").strip()
    sha_url = str(sha.get("browser_download_url") or "").strip()
    if not exe_url or not sha_url:
        raise UpdateError(f"Релиз {tag} содержит некорректные ссылки на файлы обновления.")
    return UpdateInfo(
        version=remote_version,
        tag=tag,
        exe_url=exe_url,
        sha_url=sha_url,
        notes=str(data.get("body") or "").strip(),
    )


def _download(url: str, path: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(path, "wb") as out:
        while True:
            block = resp.read(1024 * 1024)
            if not block:
                break
            out.write(block)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _parse_sha256(text: str) -> str:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    if not m:
        raise UpdateError("В файле SHA-256 не найдено корректной контрольной суммы.")
    return m.group(1).lower()


def download_update(info: UpdateInfo, update_root: str):
    """Скачивает EXE и проверяет SHA-256. Возвращает путь к новому EXE."""
    folder = Path(update_root) / f"v{info.version}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "UchetZakupok.exe.new"
    sha_path = folder / "UchetZakupok.exe.sha256"
    _download(info.sha_url, str(sha_path), timeout=15.0)
    _download(info.exe_url, str(target), timeout=120.0)
    expected = _parse_sha256(sha_path.read_text(encoding="utf-8", errors="replace"))
    actual = sha256_file(str(target))
    if actual != expected:
        try:
            target.unlink()
        except OSError:
            pass
        raise UpdateError("Контрольная сумма обновления не совпала. Файл не будет установлен.")
    return str(target)


def _psq(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_windows_update_script(pid: int, current_exe: str, new_exe: str, backup_exe: str, log_path: str) -> str:
    """PowerShell updater with rollback if the new EXE immediately fails."""
    return f'''$ErrorActionPreference = "Stop"
$pidToWait = {int(pid)}
$target = {_psq(current_exe)}
$source = {_psq(new_exe)}
$backup = {_psq(backup_exe)}
$log = {_psq(log_path)}
try {{
    try {{ Wait-Process -Id $pidToWait -ErrorAction SilentlyContinue }} catch {{}}
    Start-Sleep -Milliseconds 700
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
    if (Test-Path -LiteralPath $target) {{
        Copy-Item -LiteralPath $target -Destination $backup -Force
    }}
    Copy-Item -LiteralPath $source -Destination $target -Force

    $newProcess = Start-Process -FilePath $target -PassThru
    Start-Sleep -Seconds 5
    if ($newProcess.HasExited) {{
        throw "Новая версия завершилась сразу после запуска."
    }}

    Remove-Item -LiteralPath $source -Force -ErrorAction SilentlyContinue
    Add-Content -LiteralPath $log -Value ("[" + (Get-Date -Format "HH:mm:ss") + "] Обновление установлено успешно")
}} catch {{
    $updateError = $_.Exception.Message
    try {{
        if (Test-Path -LiteralPath $backup) {{
            Copy-Item -LiteralPath $backup -Destination $target -Force
            Start-Process -FilePath $target
        }}
    }} catch {{}}
    try {{
        Add-Content -LiteralPath $log -Value ("[" + (Get-Date -Format "HH:mm:ss") + "] Ошибка обновления, выполнен откат: " + $updateError)
    }} catch {{}}
}}
'''


def launch_windows_installer(new_exe: str, update_root: str, log_path: str):
    if not sys.platform.startswith("win"):
        raise UpdateError("Автоматическая замена EXE поддерживается только в Windows.")
    if not getattr(sys, "frozen", False):
        raise UpdateError("Автообновление устанавливается только для собранного UchetZakupok.exe.")

    current_exe = os.path.abspath(sys.executable)
    if not os.path.isfile(new_exe):
        raise UpdateError("Скачанный файл обновления не найден.")
    if not os.access(os.path.dirname(current_exe), os.W_OK):
        raise UpdateError(
            "Папка программы защищена от записи. Переместите приложение в обычную папку пользователя "
            "(например, Документы\\UchetZakupok), чтобы автообновление могло заменять EXE."
        )

    previous_dir = os.path.join(update_root, "previous")
    os.makedirs(previous_dir, exist_ok=True)
    backup_exe = os.path.join(previous_dir, f"UchetZakupok-v{__version__}.exe")
    fd, script_path = tempfile.mkstemp(prefix="uchet_update_", suffix=".ps1", dir=update_root, text=True)
    os.close(fd)
    script = build_windows_update_script(os.getpid(), current_exe, new_exe, backup_exe, log_path)
    Path(script_path).write_text(script, encoding="utf-8-sig")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", script_path,
        ],
        close_fds=True,
        creationflags=creationflags,
    )
    return script_path