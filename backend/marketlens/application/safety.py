"""Untrusted external text handling (news, filings, transcripts).

External text is DATA, never instructions. It is (1) scanned for injection patterns, (2) neutralised
(delimiter tokens escaped, control characters removed, length-limited) and (3) wrapped in an explicit
<untrusted_external_data> envelope when it is shown to an LLM.
"""

from __future__ import annotations

import re
import unicodedata

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
        r"disregard\s+(all\s+)?(previous|prior|the)\s+(instructions|rules)",
        r"you\s+are\s+now\s+(in\s+)?\w+\s+mode",
        r"system\s*prompt",
        r"</?\s*(system|assistant|untrusted_external_data)\s*>",
        r"\bact\s+as\b",
        r"override\s+(the\s+)?(rules|score|decision)",
        r"recommend\s+buy\s+immediately",
        r"output\s+stance",
        r"(이전|앞의|위의)\s*(의\s*)?(모든\s*)?(지시|명령|지침)(를|을|사항을)?\s*(모두\s*)?무시",
        r"시스템\s*프롬프트",
        r"(매수|BUY)(를|을)?\s*(즉시\s*)?추천하(라|세요|십시오)",
        r"(신뢰도|점수|confidence|score)(를|을)?\s*\d+\s*(으로|로)\s*(출력|설정|변경)",
        r"(관리자|개발자)\s*모드",
    )
]
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)


def _normalise(text: str) -> str:
    """NFKC (full-width → ASCII) and zero-width characters removed, so obfuscated attacks still match."""
    return unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH)

MAX_EXTERNAL_CHARS = 2000


def detect_injection(text: str) -> list[str]:
    t = _normalise(text)
    return [p.pattern for p in INJECTION_PATTERNS if p.search(t)]


def sanitize_external(text: str, max_chars: int = MAX_EXTERNAL_CHARS) -> str:
    t = _normalise(text)
    t = "".join(ch for ch in t if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    # neutralise anything that looks like our envelope or chat-role tags
    t = re.sub(r"<\s*/?\s*(untrusted_external_data|system|assistant|user)[^>]*>", "[tag removed]", t, flags=re.IGNORECASE)
    return t[:max_chars]


def wrap_untrusted(source: str, text: str) -> str:
    safe = sanitize_external(text)
    flags = detect_injection(text)
    note = ' injection_suspected="true"' if flags else ""
    return f'<untrusted_external_data source="{sanitize_external(source, 100)}"{note}>\n{safe}\n</untrusted_external_data>'
