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

# --- Animated walkthrough (the cinematic "how the machine works", real data) -----
# Refresh the animation's embedded DATA from live state (backtest curve/metrics,
# the FX book, and a bounded live momentum run) and publish it. Each section is
# individually tolerant; on a hard failure we fall back to the committed
# snapshot so the deploy never breaks.
python scripts/build_walkthrough.py public/walkthrough.html \
  || cp docs/explainer/how-it-works.html public/walkthrough.html \
  || echo "skip walkthrough"

# --- The landing page IS the terminal ------------------------------------------
# One static page baking every book + the ALL-ACCOUNTS overview, switcher live.
python -m trading_algo.dashboard.export --site $SYNTH -o public/index.html \
  || echo "skip terminal index (books.html will be copied as the landing page)"

# --- Directory page (books.html): every page on the site ------------------------
{
  echo '<!doctype html><meta charset=utf-8><title>MOMENTUM/3R — paper books</title>'
  echo '<meta name=viewport content="width=device-width,initial-scale=1">'
  echo '<style>body{font-family:ui-monospace,Menlo,Consolas,monospace;background:#060606;color:#c9e8cc;margin:0;padding:2.5rem;max-width:820px}'
  echo 'h1{font-size:1.1rem;letter-spacing:.1em;color:#eaffec;margin:0 0 .2rem}h1 b{color:#7ee787}'
  echo 'h2{margin:2rem 0 .6rem;font-size:.75rem;letter-spacing:.14em;color:#61805f}.s{color:#61805f;font-size:.8rem;margin:0 0 1.6rem}'
  echo 'a.card{display:block;margin:.55rem 0;padding:.9rem 1.1rem;border:1px solid #262626;border-radius:4px;background:#0d0d0d;color:#c9e8cc;text-decoration:none}'
  echo 'a.card:hover{border-color:#2a4a2c;background:#111111}.n{font-size:.95rem;font-weight:600;color:#7ee787;letter-spacing:.06em}.d{color:#61805f;font-size:.75rem;margin-top:.2rem}</style>'
  echo "<h1>■ <b>MOMENTUM/3R</b> — PAPER BOOKS</h1><p class=s>updated $(date -u '+%Y-%m-%d %H:%M UTC') · reported in AUD</p>"
  echo '<a class=card href="index.html" style="border-color:#2a4a2c"><div class=n>THE TERMINAL — ALL ACCOUNTS</div><div class=d>every paper book behind one account switcher · overview / positions / backtest / method</div></a>'
  echo '<a class=card href="walkthrough.html" style="border-color:#2a4a2c"><div class=n>▶ ANIMATED WALKTHROUGH</div><div class=d>the whole machine in motion — universe → momentum → filter → size → combine, plus the FX engine · real data</div></a>'
  echo '<a class=card href="how.html"><div class=n>HOW IT WORKS</div><div class=d>a plain-English flow diagram of what the system does and why</div></a>'
  echo '<h2>SINGLE-BOOK TERMINAL PAGES</h2>'
  for f in public/eq_*.html; do [ -e "$f" ] || continue; b=$(basename "$f" .html); n=${b#eq_}
    echo "<a class=card href=\"$b.html\"><div class=n>${n^^} · EQUITIES</div><div class=d>equity momentum book — terminal</div></a>"; done
  for f in public/tm_*.html; do [ -e "$f" ] || continue; b=$(basename "$f" .html); n=${b#tm_}
    echo "<a class=card href=\"$b.html\"><div class=n>${n^^} · AGENT BOOK</div><div class=d>FX / multi-asset agent book — terminal</div></a>"; done
  echo '<h2>CLASSIC FX CANDLESTICK PAGES — PER-TRADE DETAIL</h2>'
  for f in public/fx_*.html; do [ -e "$f" ] || continue; b=$(basename "$f" .html); n=${b#fx_}
    echo "<a class=card href=\"$b.html\"><div class=n>${n^^}</div><div class=d>candlesticks + the reasoning behind every trade</div></a>"; done
} > public/books.html

# If the terminal index failed to build, the directory page is the landing.
[ -e public/index.html ] || cp public/books.html public/index.html
