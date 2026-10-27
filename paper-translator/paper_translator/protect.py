from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from .common import PipelineError, sha

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_PATTERN = rf"\b\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}\b|\d{{4}}年\d{{1,2}}月\d{{1,2}}日"
DATE_RE = re.compile(DATE_PATTERN)


def date_key(value):
    if not DATE_RE.fullmatch(value):
        return None
    try:
        parsed = datetime.strptime(value, "%Y年%m月%d日" if "年" in value else "%d %B %Y")
    except ValueError:
        return None
    return parsed.year, parsed.month, parsed.day


def localize_dates(text):
    def replace(match):
        key = date_key(match[0])
        return f"{key[0]}年{key[1]}月{key[2]}日" if key else match[0]
    return PROTECTED.sub(replace, text)


# Order matters: math and whole links are protected before numbers/macros within them.
PROTECTED = re.compile(
    r"\$\$[\s\S]*?\$\$|(?<![\\$])\$(?!\$)(?:\\.|[^$])*?(?<!\\)\$"
    r"|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]"
    r'|<a\s+id="[^"]+"\s*></a>'
    r"|</?[A-Za-z][^>\n]*>"
    r"|!?\[[^\]\n]*\]\([^\)\n]+\)|\[\^[^\]]+\](?::)?"
    r"|\\[A-Za-z]+(?:\*?)(?:\{[^{}]*\})*"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    rf"|(?:{DATE_PATTERN})"
    r"|(?<![A-Za-z0-9_])[-+−]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?(?:%)?"
)
TOKEN = re.compile(r"⟦PT_[A-Za-z0-9_]+⟧")


def protect(text, block_id):
    mapping = {}
    prefix = sha(block_id)[:8]

    def replace(match):
        token = f"⟦PT_{prefix}_{len(mapping):04d}⟧"
        mapping[token] = match[0]
        return token

    if "⟦PT_" in text:
        raise PipelineError("Source collides with placeholder namespace")
    return PROTECTED.sub(replace, text), mapping


def restore(text, mapping):
    actual = Counter(TOKEN.findall(text))
    expected = Counter({k: 1 for k in mapping})
    if actual != expected:
        raise PipelineError(f"Placeholder missing/duplicate/unknown: expected {dict(expected)}, got {dict(actual)}")
    for token, original in mapping.items():
        text = text.replace(token, original)
    if "⟦PT_" in text:
        raise PipelineError("Malformed/unrestored placeholder")
    return text


def invariants(text):
    return Counter(("date:" + "-".join(map(str, date_key(m)))) if date_key(m) else m for m in PROTECTED.findall(text))


def natural_text(text):
    value = PROTECTED.sub(" ", text)
    return re.sub(r"[#*|<>:_`-]", " ", value).strip()
