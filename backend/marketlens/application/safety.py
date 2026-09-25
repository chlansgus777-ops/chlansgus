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
    )
]

MAX_EXTERNAL_CHARS = 2000


def detect_injection(text: str) -> list[str]:
    return [p.pattern for p in INJECTION_PATTERNS if p.search(text or "")]


def sanitize_external(text: str, max_chars: int = MAX_EXTERNAL_CHARS) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = "".join(ch for ch in t if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    # neutralise anything that looks like our envelope or chat-role tags
    t = re.sub(r"<\s*/?\s*(untrusted_external_data|system|assistant|user)[^>]*>", "[tag removed]", t, flags=re.IGNORECASE)
    return t[:max_chars]


def wrap_untrusted(source: str, text: str) -> str:
    safe = sanitize_external(text)
    flags = detect_injection(text)
    note = ' injection_suspected="true"' if flags else ""
    return f'<untrusted_external_data source="{sanitize_external(source, 100)}"{note}>\n{safe}\n</untrusted_external_data>'
