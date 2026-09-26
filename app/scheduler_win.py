# -*- coding: utf-8 -*-
"""Интеграция с Планировщиком заданий Windows для фоновых e-mail напоминаний."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from dataclasses import dataclass

import db

TASK_NAME = "UchetZakupok - Signing Reminder"
TASK_DESCRIPTION = "Ежедневная сводка «Требует внимания»: подписание, исполнение, оплата и склад."


class SchedulerError(RuntimeError):
    pass


@dataclass
class TaskCommand:
    executable: str
    arguments: str
    working_directory: str


def is_windows() -> bool:
    return sys.platform.startswith("win")


def normalize_time(value: str) -> str:
    text = (value or "").strip()
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if not m:
        raise ValueError("Время должно быть в формате ЧЧ:ММ, например 09:00.")
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def task_command() -> TaskCommand:
    root = db.app_root_dir()
    if getattr(sys, "frozen", False):
        return TaskCommand(os.path.abspath(sys.executable), "--reminders-only", root)

    main_py = os.path.join(root, "main.py")
    exe = os.path.abspath(sys.executable)
    if is_windows():
        folder = os.path.dirname(exe)
        candidate = os.path.join(folder, "pythonw.exe")
        if os.path.exists(candidate):
            exe = candidate
    # Аргументы передаются отдельной строкой в New-ScheduledTaskAction.
    args = f'"{os.path.abspath(main_py)}" --reminders-only'
    return TaskCommand(exe, args, root)


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    if not is_windows():
        raise SchedulerError("Планировщик заданий доступен только в Windows.")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SchedulerError(f"Не удалось запустить PowerShell: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "неизвестная ошибка").strip()
        raise SchedulerError(f"Ошибка Планировщика Windows: {detail}")
    return proc


def install_daily_task(time_hhmm: str) -> dict:
    """Создаёт/обновляет ежедневную задачу для текущего интерактивного пользователя."""
    hhmm = normalize_time(time_hhmm)
    cmd = task_command()
    script = f"""
$ErrorActionPreference = 'Stop'
$taskName = {_ps_quote(TASK_NAME)}
$action = New-ScheduledTaskAction -Execute {_ps_quote(cmd.executable)} -Argument {_ps_quote(cmd.arguments)} -WorkingDirectory {_ps_quote(cmd.working_directory)}
$trigger = New-ScheduledTaskTrigger -Daily -At {_ps_quote(hhmm)}
$userId = "$env:USERDOMAIN\\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description {_ps_quote(TASK_DESCRIPTION)} -Force | Out-Null
Write-Output 'OK'
"""
    _run_powershell(script)
    return {"installed": True, "time": hhmm, "command": cmd}


def remove_task() -> bool:
    if not is_windows():
        return False
    script = f"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue
if ($null -ne $task) {{
  Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Confirm:$false
  Write-Output 'REMOVED'
}} else {{
  Write-Output 'ABSENT'
}}
"""
    proc = _run_powershell(script)
    return "REMOVED" in (proc.stdout or "")


def task_info() -> dict:
    if not is_windows():
        return {"installed": False, "supported": False}
    script = f"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue
if ($null -eq $task) {{ Write-Output 'ABSENT'; exit 0 }}
$info = Get-ScheduledTaskInfo -TaskName {_ps_quote(TASK_NAME)}
$action = $task.Actions | Select-Object -First 1
$trigger = $task.Triggers | Select-Object -First 1
[PSCustomObject]@{{
  State = [string]$task.State
  LastRunTime = $info.LastRunTime.ToString('s')
  LastTaskResult = $info.LastTaskResult
  NextRunTime = $info.NextRunTime.ToString('s')
  Execute = [string]$action.Execute
  Arguments = [string]$action.Arguments
}} | ConvertTo-Json -Compress
"""
    try:
        proc = _run_powershell(script)
    except SchedulerError as exc:
        return {"installed": False, "supported": True, "error": str(exc)}
    out = (proc.stdout or "").strip()
    if not out or out.endswith("ABSENT"):
        return {"installed": False, "supported": True}
    import json
    try:
        data = json.loads(out.splitlines()[-1])
    except Exception:
        return {"installed": True, "supported": True, "raw": out}
    return {
        "installed": True,
        "supported": True,
        "state": data.get("State"),
        "last_run": data.get("LastRunTime"),
        "last_result": data.get("LastTaskResult"),
        "next_run": data.get("NextRunTime"),
        "execute": data.get("Execute"),
        "arguments": data.get("Arguments"),
    }


def run_task_now() -> None:
    if not is_windows():
        raise SchedulerError("Планировщик заданий доступен только в Windows.")
    script = f"""
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue
if ($null -eq $task) {{ throw 'Задача не установлена.' }}
Start-ScheduledTask -TaskName {_ps_quote(TASK_NAME)}
Write-Output 'OK'
"""
    _run_powershell(script)