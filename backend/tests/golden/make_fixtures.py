"""Regenerate the frozen point-in-time golden fixtures (run manually; commit the JSON)."""

import json
from pathlib import Path

from marketlens.application.codec import encode
from tests.fixtures import analysis

OUT = Path(__file__).parent / "fixtures"
GOLDEN = ("NVDA", "AMZN", "JPM", "XOM", "TSM")

if __name__ == "__main__":
    for t in GOLDEN:
        _res, inp = analysis(t)
        (OUT / f"{t}.json").write_text(json.dumps(encode(inp), sort_keys=True))
        print("wrote", t)
