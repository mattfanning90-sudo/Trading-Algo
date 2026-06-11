# Obsidian vault — Multi-Region Momentum

This folder is a self-contained **Obsidian vault** documenting the trading
system, committed inside the repo so notes are version-controlled with the code.

## Open it
**Obsidian → Open folder as vault →** select this `obsidian/` folder. Start at the
**Multi-Region Momentum** note.

## Keep it in sync
- **Read-only:** `git pull` in the repo — notes update on disk and Obsidian
  reflects them.
- **Two-way:** install the **Obsidian Git** community plugin; it detects the
  repo's `.git` and lets you pull/push from inside Obsidian.

## Regenerate
`Reference.md` is generated from `trading_algo/regions.py` and `config.py`:

```bash
make obsidian        # or: python tools/build_obsidian_vault.py
```

Notes use wikilinks, tags, callouts, MathJax and Mermaid — all native to Obsidian.
