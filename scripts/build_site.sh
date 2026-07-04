#!/usr/bin/env bash
# Shared site builder for the two Pages-publishing workflows (day-paper.yml and
# fx-paper.yml). ONE copy of the export + landing-page logic so the public site
# can never flip-flop between two drifting versions of the same steps.
#
# Env contract (defensive defaults — a missing env degrades to the standard
# layout rather than silently exporting nothing):
#   FX_STATE_DIR        — where fx_state_*.json live       (default: state)
#   MOMENTUM_STATE_DIR  — where paper_state_*.json live    (default: state)
#   SYNTH               — '--synthetic' for offline runs   (default: empty)
#   NEWS_API_KEY        — optional FMP key, read by the dashboard exporters
set -euo pipefail

FX_STATE_DIR="${FX_STATE_DIR:-state}"
MOMENTUM_STATE_DIR="${MOMENTUM_STATE_DIR:-state}"
SYNTH="${SYNTH:-}"

mkdir -p public

# Keep the persisted parquet cache (actions/cache) from growing without bound.
find trading_algo/forex/.cache -mtime +7 -delete 2>/dev/null || true

# --- FX candlestick pages, one per book --------------------------------------
# Deliberately NOT the dashboard module's --all mode: that also runs
# build_index(), whose public/index.html the heredoc below would overwrite
# seconds later — the bash heredoc is the single CI index; build_index remains
# for local --all/--index runs only. The per-account '|| echo skip' preserves
# the old whole-step tolerance but per-book, so one bad book no longer risks
# the others.
for f in "$FX_STATE_DIR"/fx_state_*.json; do
  [ -e "$f" ] || continue
  name=$(basename "$f" .json); name=${name#fx_state_}
  python -m trading_algo.forex.dashboard --account "$name" \
    -o "public/fx_${name}.html" $SYNTH || echo "skip fx $name"
done

# --- Equity-momentum pages ----------------------------------------------------
# Every Pages deploy replaces the WHOLE site, so these must be re-exported even
# by the hourly workflow or they'd vanish until the nightly run.
for f in "$MOMENTUM_STATE_DIR"/paper_state_*.json; do
  [ -e "$f" ] || continue
  name=$(basename "$f" .json); name=${name#paper_state_}
  python -m trading_algo.dashboard.export --account "$name" $SYNTH \
    -o "public/eq_${name}.html" || echo "skip equity $name"
done

# --- Momentum/3R terminal pages for the FX / multi-asset books -----------------
# The equity eq_*.html exports above already ARE the terminal; this gives the
# FX books the same page set (the classic fx_*.html candlestick pages stay).
for f in "$FX_STATE_DIR"/fx_state_*.json; do
  [ -e "$f" ] || continue
  name=$(basename "$f" .json); name=${name#fx_state_}
  python -m trading_algo.dashboard.export --account "$name" $SYNTH \
    -o "public/tm_${name}.html" || echo "skip terminal $name"
done

# --- "How it works" page --------------------------------------------------------
python -c "from trading_algo.forex.dashboard import build_how_page; build_how_page(\"public\")" \
  || echo "skip how page"

# --- Single canonical landing page (the CI index) -------------------------------
{
  echo '<!doctype html><meta charset=utf-8><title>Trading Paper Books</title>'
  echo '<meta name=viewport content="width=device-width,initial-scale=1">'
  echo '<style>body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:3rem;max-width:760px}'
  echo 'h1{margin:0 0 .25rem}h2{margin:2rem 0 .5rem;font-size:1rem;color:#8b949e}.s{color:#8b949e;margin:0 0 1rem}'
  echo 'a.card{display:block;margin:.8rem 0;padding:1.1rem 1.4rem;border:1px solid #30363d;border-radius:14px;background:#161b22;color:#e6edf3;text-decoration:none}'
  echo 'a.card:hover{border-color:#58a6ff}.n{font-size:1.15rem;font-weight:600;color:#58a6ff}.d{color:#8b949e;font-size:.85rem}</style>'
  echo "<h1>Trading Paper Books</h1><p class=s>updated $(date -u '+%Y-%m-%d %H:%M UTC')</p>"
  echo '<a class=card href="how.html" style="border-color:#1f6feb"><div class=n>📖 How it works — start here</div><div class=d>a plain-English flow diagram of what the system does and why</div></a>'
  echo '<h2>Momentum/3R terminal — every paper book</h2>'
  for f in public/eq_*.html; do [ -e "$f" ] || continue; b=$(basename "$f" .html); n=${b#eq_}
    echo "<a class=card href=\"$b.html\"><div class=n>$n · terminal</div><div class=d>equity momentum book — overview / positions / backtest / method</div></a>"; done
  for f in public/tm_*.html; do [ -e "$f" ] || continue; b=$(basename "$f" .html); n=${b#tm_}
    echo "<a class=card href=\"$b.html\"><div class=n>$n · terminal</div><div class=d>FX / multi-asset agent book — decision book, agent votes, pair charts</div></a>"; done
  echo '<h2>Classic FX candlestick pages — per-trade detail</h2>'
  for f in public/fx_*.html; do [ -e "$f" ] || continue; b=$(basename "$f" .html); n=${b#fx_}
    echo "<a class=card href=\"$b.html\"><div class=n>$n</div><div class=d>candlesticks + the reasoning behind every trade</div></a>"; done
} > public/index.html
