"""Per-region tradable universes (Yahoo Finance tickers).

These are *current* liquid constituents, which means the backtest has
survivorship bias (names that dropped out of the index aren't here). Treat
absolute backtest numbers as an upper bound — see README "Known limitations".
Refresh these lists periodically as index membership changes.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# ASX — liquid large caps (Yahoo .AX suffix). Unchanged from the original sleeve.
# ---------------------------------------------------------------------------
ASX = [
    "BHP.AX", "CBA.AX", "CSL.AX", "NAB.AX", "WBC.AX", "ANZ.AX", "MQG.AX",
    "WES.AX", "GMG.AX", "FMG.AX", "RIO.AX", "TLS.AX", "WDS.AX", "TCL.AX",
    "WOW.AX", "ALL.AX", "REA.AX", "WTC.AX", "QBE.AX", "SUN.AX", "COL.AX",
    "XRO.AX", "CPU.AX", "STO.AX", "JHX.AX", "ORG.AX", "RMD.AX", "COH.AX",
    "SHL.AX", "IAG.AX", "CAR.AX", "NST.AX", "SCG.AX", "ASX.AX", "MIN.AX",
    "PME.AX", "SGP.AX", "FPH.AX", "TWE.AX", "QAN.AX", "BXB.AX", "AGL.AX",
    "S32.AX", "EVN.AX", "AMC.AX", "MPL.AX", "SEK.AX", "ALD.AX", "LYC.AX",
    "JBH.AX", "A2M.AX", "BSL.AX", "PLS.AX", "IGO.AX", "NXT.AX", "HVN.AX",
]

# ---------------------------------------------------------------------------
# US — S&P 500 large caps (Yahoo: no suffix).
# ---------------------------------------------------------------------------
US_STOCKS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "BRK-B", "TSLA", "LLY",
    "AVGO", "JPM", "V", "UNH", "XOM", "MA", "JNJ", "PG", "HD", "COST", "ORCL",
    "MRK", "ABBV", "CVX", "ADBE", "CRM", "PEP", "KO", "BAC", "AMD", "NFLX",
    "TMO", "WMT", "ACN", "LIN", "MCD", "CSCO", "ABT", "DHR", "QCOM", "TXN",
    "INTU", "WFC", "PM", "AMGN", "IBM", "GE", "CAT", "NOW", "GS", "ISRG",
    "SPGI", "HON", "AXP", "BKNG", "NEE", "LOW", "UNP", "RTX", "PFE", "AMAT",
    "BA", "COP", "BLK", "DE", "ELV", "SYK", "MDT", "LMT", "PLD", "ADI",
    "MDLZ", "GILD", "MMC", "REGN", "VRTX", "CB", "SBUX", "MU", "C", "ADP",
    "SCHW", "BMY", "SO", "ZTS", "CI", "MO", "DUK", "BSX", "TJX", "PGR",
]

# US — major ETFs (broad, sector, factor, thematic, fixed income, commodity).
US_ETFS = [
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "VEA", "VWO", "EFA", "EEM",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
    "XLC", "SMH", "SOXX", "IBB", "XBI", "ITB", "KRE", "JETS", "ARKK",
    "GLD", "SLV", "TLT", "IEF", "HYG", "LQD", "VNQ",
]

US = US_STOCKS + US_ETFS

# ---------------------------------------------------------------------------
# FTSE — FTSE 100 liquid names (Yahoo .L suffix; prices quoted in pence/GBX).
# ---------------------------------------------------------------------------
FTSE = [
    "AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "BP.L", "RIO.L", "GSK.L", "DGE.L",
    "BATS.L", "GLEN.L", "REL.L", "RR.L", "LSEG.L", "NG.L", "BARC.L", "LLOY.L",
    "NWG.L", "PRU.L", "CPG.L", "AAL.L", "BA.L", "VOD.L", "TSCO.L", "NXT.L",
    "AHT.L", "EXPN.L", "IMB.L", "SSE.L", "ANTO.L", "STAN.L", "FLTR.L", "AV.L",
    "LGEN.L", "IHG.L", "RKT.L", "SGE.L", "INF.L", "WTB.L", "SMIN.L", "BNZL.L",
    "RTO.L", "ITRK.L", "DCC.L", "MNDI.L", "ADM.L", "PSON.L", "SVT.L", "UU.L",
    "ENT.L", "AUTO.L", "HLN.L", "PSN.L", "BDEV.L", "TW.L", "LAND.L", "SGRO.L",
    "BKG.L", "CCH.L", "CNA.L", "FRES.L", "KGF.L", "SBRY.L", "JD.L", "WEIR.L",
    "PHNX.L", "HIK.L", "SDR.L", "RMV.L", "BEZ.L", "MRO.L",
]


# Convenience map (region key -> universe). Mirrors regions.REGIONS keys.
UNIVERSES: dict[str, list[str]] = {
    "ASX": ASX,
    "US": US,
    "FTSE": FTSE,
}

# ---------------------------------------------------------------------------
# Trend / multi-asset sleeve — a diversified basket of liquid US-listed ETFs
# spanning four asset classes. Traded *time-series* (each asset long/short on
# its own trend), this is the diversifier sleeve (see trend.py). All USD, all
# deep-liquidity; common history starts ~2007 (UUP launched Feb-2007), which
# conveniently spans the 2008, 2020 and 2022 stress tests.
# ---------------------------------------------------------------------------
TREND_ETFS: dict[str, list[str]] = {
    "equity":      ["SPY", "EFA", "EEM"],   # US, developed ex-US, emerging
    "bond":        ["IEF", "TLT", "LQD"],   # 7-10y UST, 20y+ UST, IG credit
    "commodity":   ["DBC", "GLD"],          # broad commodities, gold
    "currency":    ["UUP"],                 # US dollar index (FX proxy)
}

# Flat list of all trend instruments.
TREND = [t for group in TREND_ETFS.values() for t in group]

# ---------------------------------------------------------------------------
# Carry sleeve — a cross-section of yield-bearing US-listed ETFs spanning the
# duration/credit/asset-class spectrum, so the *spread* of income yields (and
# its time-variation) is a meaningful cross-sectional signal: SHY/SPY/GLD sit at
# the low-carry end, HYG/VNG/EMB/TLT at the high-carry end, and the ranking
# rotates as credit spreads and the curve move. Traded L/S on carry (see carry.py).
# ---------------------------------------------------------------------------
CARRY_ETFS: dict[str, list[str]] = {
    "rates":     ["SHY", "IEF", "TLT", "TIP"],   # short/7-10y/20y+ UST, TIPS
    "credit":    ["LQD", "HYG", "EMB"],          # IG, high-yield, EM USD bonds
    "equity":    ["SPY", "EFA", "EEM"],          # US, developed ex-US, emerging
    "real":      ["VNQ", "GLD"],                 # REITs (high yield), gold (~zero)
}

# Flat list of all carry instruments.
CARRY = [t for group in CARRY_ETFS.values() for t in group]

