"""Where two results.json differ (the measurement and its reproduction should be byte-equal):

    python -m marketlens.backtest.diff bt-out/results.json bt-out-repro/results.json

Prints how many values differ under each top-level key and the first differing paths with both values."""

from __future__ import annotations

import json
import sys
from typing import Any, Iterator


def differences(a: Any, b: Any, path: str = "") -> Iterator[tuple[str, Any, Any]]:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                yield f"{path}/{k}", a.get(k, "<missing>"), b.get(k, "<missing>")
            else:
                yield from differences(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield f"{path}[len]", len(a), len(b)
        for i, (x, y) in enumerate(zip(a, b)):
            yield from differences(x, y, f"{path}[{i}]")
    elif a != b:
        yield path, a, b


def main(argv: list[str] | None = None) -> int:
    pa, pb = (argv or sys.argv[1:])[:2]
    with open(pa, encoding="utf-8") as fa, open(pb, encoding="utf-8") as fb:
        a, b = json.load(fa), json.load(fb)
    diffs = list(differences(a, b))
    per_key: dict[str, int] = {}
    for p, _x, _y in diffs:
        top = p.split("/")[1].split("[")[0] if p.startswith("/") else p
        per_key[top] = per_key.get(top, 0) + 1
    print(json.dumps({"differences": len(diffs), "per_top_level_key": per_key}, ensure_ascii=False))
    for p, x, y in diffs[:80]:
        print(p, "|", json.dumps(x, ensure_ascii=False)[:160], "|", json.dumps(y, ensure_ascii=False)[:160])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
