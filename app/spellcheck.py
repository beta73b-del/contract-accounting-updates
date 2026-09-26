# -*- coding: utf-8 -*-
"""Проверка русского правописания через pyspellchecker.

SpellChecker создаётся лениво при первом вызове проверки. Это важно для
PyInstaller: даже если словарь по какой-либо причине не попал в сборку,
приложение продолжит запускаться и выдаст понятную диагностику только при
обращении к проверке правописания.
"""
import re

try:
    from spellchecker import SpellChecker
except ImportError:
    SpellChecker = None

_spell = None
_spell_init_error = None

WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z]{3,}")


def _get_spell():
    global _spell, _spell_init_error

    if _spell is not None:
        return _spell

    if SpellChecker is None:
        raise RuntimeError(
            "pyspellchecker не установлен. Установите пакет командой: "
            "python -m pip install pyspellchecker"
        )

    if _spell_init_error is not None:
        raise RuntimeError(_spell_init_error)

    try:
        _spell = SpellChecker(language="ru")
        return _spell
    except Exception as exc:
        # Наиболее частый случай в EXE: PyInstaller не включил
        # spellchecker/resources/ru.json.gz.
        _spell_init_error = (
            "Не удалось загрузить русский словарь pyspellchecker. "
            "Если приложение запущено из EXE, пересоберите его с включением "
            "данных пакета spellchecker (ru.json.gz). "
            f"Техническая причина: {exc}"
        )
        raise RuntimeError(_spell_init_error) from exc


def check_text_spelling(text: str):
    spell = _get_spell()
    words = WORD_RE.findall(text or "")
    # Не считаем полностью заглавные сокращения и латинские артикулы ошибками.
    candidates = [w.lower() for w in words if not (w.isupper() and len(w) <= 8)]
    unknown = spell.unknown(candidates)
    result = []
    seen = set()
    for word in candidates:
        if word in unknown and word not in seen:
            suggestions = list(spell.candidates(word) or [])
            # Совместимость с разными версиями pyspellchecker: WordFrequency
            # не имеет стабильного публичного метода frequency().
            correction_fn = getattr(spell, "correction", None)
            best = correction_fn(word) if callable(correction_fn) else None
            suggestions = sorted(set(suggestions), key=lambda x: (x or ""))
            if best:
                suggestions = [best] + [x for x in suggestions if x != best]
            suggestions = suggestions[:5]
            result.append((word, suggestions))
            seen.add(word)
    return result