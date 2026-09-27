"""Collect REAL outlook sentences from SEC 8-K (Item 2.02) Exhibit 99 press releases for the guidance corpus.

Runs on a GitHub-hosted runner (the development sandbox has no route to sec.gov). SEC fair access: a User-Agent that
names the requester, and the provider's own client-side rate limit (well under 10 requests/second). Nothing is
generated: every line is a sentence as it appears in the filing, with the filing's accession number and URL.

Kept: sentences with a forward-looking word and an amount ($ or %), and every sentence with an amount in the ten
sentences after an "outlook"/"guidance" header (bulleted outlooks rarely repeat "expect"). The labels are added by hand
afterwards in backend/tests/corpus/guidance_corpus.jsonl.

    SEC_USER_AGENT="Name email" python scripts/corpus/collect_guidance_sentences.py --out candidates.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date

from marketlens.domain.guidance import FORWARD, html_to_text, sentences
from marketlens.providers.contracts import ProviderError
from marketlens.providers.live.sec_edgar import SecEdgarProvider

# companies across sectors that publish a numeric outlook in the earnings press release (and some that do not)
TICKERS = """NVDA AMD AVGO QCOM MU INTC TXN ADI MRVL LRCX AMAT KLAC ON MCHP CSCO ORCL CRM ADBE NOW INTU WDAY PANW CRWD FTNT
SNPS CDNS ANET DELL HPQ HPE NTAP WDC STX ZS DDOG SNOW MDB TEAM UBER ABNB BKNG EXPE MAR HLT CMG SBUX NKE LULU DECK TGT
WMT HD LOW BBY DG DLTR ROST TJX AZO ORLY ULTA KR GIS CPB HSY MDLZ PEP PG CL KMB CLX EL JNJ LLY MRK PFE ABBV AMGN GILD
VRTX REGN BIIB ISRG TMO DHR A IQV ZBH SYK BSX EW DXCM IDXX UNH CI HUM CVS ELV CAT DE HON MMM GE EMR ETN PH ROK ITW
DOV UPS FDX UNP CSX DAL UAL LUV LMT NOC GD RTX SLB HAL F GM RIVN DIS NFLX CMCSA CHTR T VZ TMUS PYPL V MA AXP FIS GPN
ADP PAYX SPGI MCO ZTS HCA DGX LH RMD ALGN MTD WAT PODD TER KEYS ZBRA GRMN LOGI JBL FLEX CIEN AKAM FFIV GDDY EBAY ETSY""".split()
_AMOUNT = re.compile(r"\$\s?\(?\d|\d(?:\.\d+)?\s*(%|percent)")
_HEADER = re.compile(r"(outlook|guidance|expects?|expectations)\b", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", default="2024-10-01")
    ap.add_argument("--per-company", type=int, default=3)
    args = ap.parse_args()
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        print("SEC_USER_AGENT is required (Name email)", file=sys.stderr)
        return 2
    sec = SecEdgarProvider(ua)
    seen: set[str] = set()
    n_files = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for t in TICKERS:
            try:
                rels = sec.earnings_releases(t, date.fromisoformat(args.since), max_n=args.per_company)
            except ProviderError as e:
                print(f"{t}: {e}", file=sys.stderr)
                continue
            for r in rels:
                n_files += 1
                sents = sentences(html_to_text(r["text"]))
                after_header = 0
                for s in sents:
                    low = s.lower()
                    if len(s) > 600:
                        continue
                    if _HEADER.search(s) and not _AMOUNT.search(s) and len(s) < 160:
                        after_header = 10
                        continue
                    keep = bool(_AMOUNT.search(s)) and (any(w in low for w in FORWARD) or after_header > 0)
                    after_header = max(0, after_header - 1)
                    if not keep or s in seen:
                        continue
                    seen.add(s)
                    out.write(json.dumps({"ticker": t, "accession": r["accession"], "filed_at": r["filed_at"].isoformat(),
                                          "url": r["url"], "sentence": s}, ensure_ascii=False) + "\n")
    print(f"releases read: {n_files}, sentences kept: {len(seen)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
