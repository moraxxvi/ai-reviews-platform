"""Предобработка русского текста (модуль «Предобработка текста» из СРО2, п. 2.4).

Шаги: очистка -> нормализация -> сегментация на клаузы.
Лемматизация pymorphy3 подключается, если библиотека установлена;
без неё работает упрощённая нормализация (нижний регистр + стемминг по окончаниям
не применяется, т.к. char n-граммы устойчивы к словоформам).
"""
import re

try:  # опциональная зависимость из СРО2
    import pymorphy3

    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:  # pragma: no cover - зависит от окружения
    _MORPH = None

# Эмодзи сохраняем: они несут тональность (см. СРО2, п. 2.4)
_EMOJI_POS = "👍😍😊❤🔥👌🥰"
_EMOJI_NEG = "👎😡😤😞💔🤬"
_HTML = re.compile(r"<[^>]+>")
_URL = re.compile(r"https?://\S+")
_SPACES = re.compile(r"\s+")
_KEEP = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ\s.,!?;:()\-+%₸" + _EMOJI_POS + _EMOJI_NEG + "]")

# разделители клауз: знаки препинания и противительные союзы
_CLAUSE_SPLIT = re.compile(r"[.!?;,]+|\b(?:но|зато|однако|хотя)\b", re.I)


def clean(text: str) -> str:
    """Удаляет разметку и служебные символы, нормализует регистр и пробелы."""
    text = _HTML.sub(" ", text or "")
    text = _URL.sub(" ", text)
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = _KEEP.sub(" ", text)
    text = _SPACES.sub(" ", text).strip().lower()
    return text


def emoji_tone(text: str) -> int:
    """+1 / -1 / 0 по эмодзи в тексте (используется как слабый сигнал)."""
    pos = sum(text.count(ch) for ch in _EMOJI_POS)
    neg = sum(text.count(ch) for ch in _EMOJI_NEG)
    return (pos > neg) - (neg > pos)


def split_clauses(text: str):
    """Разбивает отзыв на клаузы: разные части одного отзыва могут иметь разную тональность."""
    text = clean(text)
    parts = [p.strip(" ,-") for p in _CLAUSE_SPLIT.split(text) if p and p.strip(" ,-")]
    merged = []
    for p in parts:
        # слишком короткие обрывки («очень», «ещё») присоединяем к соседней клаузе
        if len(p.split()) < 2 and merged:
            merged[-1] += " " + p
        else:
            merged.append(p)
    return [p for p in merged if len(p) >= 3]


def lemmatize(text: str) -> str:
    """Лемматизация (pymorphy3), если доступна; иначе возвращает очищенный текст."""
    text = clean(text)
    if _MORPH is None:
        return text
    return " ".join(_MORPH.parse(w)[0].normal_form for w in text.split())


def lemmatizer_available() -> bool:
    return _MORPH is not None


def mask_personal_data(text: str) -> str:
    """Маскирует персональные данные перед записью в журнал диалогов (СРО2, п. 2.5)."""
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text or "")
    text = re.sub(r"(?<!\d)(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)", "[телефон]", text)
    text = re.sub(r"(?<!\d)\d{12}(?!\d)", "[иин]", text)
    text = re.sub(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", "[карта]", text)
    return text
