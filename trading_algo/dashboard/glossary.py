"""Plain words for the terms the book screens can't avoid (Matt's standing
rule: plain English first, the term second). Each term is explained the first
time it appears on a screen. Matching runs on the original text only, so a
gloss is never itself glossed."""
from __future__ import annotations

import re

# (term, pattern, gloss) — longer terms first.
TERMS: list[tuple[str, str, str]] = [
    ("Deflated-Sharpe", r"\bDeflated-Sharpe\b",
     "a Sharpe score marked down for how many versions of the strategy were tried, so luck isn't mistaken for skill"),
    ("Sharpe", r"(?<![-\w])Sharpe\b",
     "return per unit of ups and downs after the return on cash; above 1 is strong, below 0 did worse than cash"),
    ("permutation test", r"\bpermutation test\b",
     "re-running the search on shuffled, patternless prices to see how often luck alone does as well"),
    ("p-value", r"\bp-value\b", "the share of shuffled runs that did at least as well"),
    ("profit factor", r"\bprofit factor\b",
     "money won on winning positions divided by money lost on losing ones; above 1 means winners paid for losers"),
    ("survivorship-biased", r"\bsurvivorship-biased\b",
     "tested only on companies that still exist today, which flatters the result"),
    ("signal horizon", r"\bsignal horizon\b", "how far ahead the signal that opens a position is looking"),
    ("volatility target", r"\bvolatility target\b",
     "positions are sized so the book's expected ups and downs stay near a set level"),
    ("long-term trend", r"\blong-term trend\b",
     "the market's own average price over a long look-back; below it, the strategy holds cash"),
    ("safety breaker", r"\bsafety breaker\b", "a rule that pauses trading if the book falls too far from its peak"),
    ("below its best", r"\bbelow its best\b", "how far the book has fallen from its highest value"),
    ("stamp duty", r"\bstamp duty\b", "a 0.5% UK tax on buying shares"),
    ("spread", r"\bspread\b", "the gap between the buying and selling price, paid on every trade"),
    ("financing", r"\bfinancing\b", "the overnight interest paid or earned on currency positions"),
    ("backtest", r"\bbacktest\b", "a replay of the strategy on past prices, costs included"),
    ("benchmark", r"\bbenchmark\b", "what simply holding the market's index funds would have done"),
    ("tracking error", r"\btracking error\b", "how much the book's return normally wanders from the market's"),
    ("basis points", r"\bbasis points\b", "hundredths of a percent; 46 basis points is 0.46%"),
]
_COMPILED = [(term, re.compile(pattern, re.IGNORECASE), g) for term, pattern, g in TERMS]


def gloss(texts: list[str]) -> list[str]:
    """`texts` in display order, each term's FIRST occurrence followed by
    ' (gloss)'. Later occurrences stay plain."""
    pending = list(_COMPILED)
    out: list[str] = []
    for text in texts:
        if not text:
            out.append(text)
            continue
        inserts: list[tuple[int, str]] = []
        taken: list[tuple[int, int]] = []
        for entry in list(pending):
            term, rx, g = entry
            for m in rx.finditer(text):
                if any(a <= m.start() < b for a, b in taken):
                    continue
                inserts.append((m.end(), g))
                taken.append((m.start(), m.end()))
                pending.remove(entry)
                break
        for end, g in sorted(inserts, reverse=True):
            text = f"{text[:end]} ({g}){text[end:]}"
        out.append(text)
    return out
