---
name: spec
description: >-
  Schema-driven spec builder for this repo: capture goals, non-goals, and
  testable acceptance criteria in docs/specs/, populated collaboratively —
  Claude drafts from repo context, the user confirms or edits each field.
  Use when the user wants to define goals or acceptance criteria for a piece
  of work, write or resume a spec, or check finished work against one.
argument-hint: "<topic> | <existing spec> | check <spec>"
---

# /spec — collaborative goals & acceptance criteria

One shared schema for agreeing what "done" means before (or while) building.
The division of labour is fixed: **Claude drafts, the user decides.** Nothing
is marked agreed until the user has confirmed it.

## Files

- Schema template: `.claude/skills/spec/template.md`
- Specs live at `docs/specs/<kebab-case-slug>.md`
- `docs/specs/_example.md` is a worked example of the schema — never treat it
  as a real spec.

## Modes — pick from the arguments

1. **New spec** (default): the argument is a topic that doesn't match an
   existing spec → copy `template.md` to `docs/specs/<slug>.md` and populate.
2. **Resume**: the argument matches a file in `docs/specs/` (by slug, title,
   or path) → load it, list every remaining `TBD`, and continue populating.
3. **Check**: the first word is `check` → audit the named spec's acceptance
   criteria against the current code and tests (see Check mode below).

With no arguments: list existing specs in `docs/specs/` with their `status`,
and ask whether to start a new one or resume.

## How to populate (new + resume)

- **Draft before asking.** Read the relevant context first — `CLAUDE.md`
  (especially the invariants), the modules the work touches, existing specs —
  and pre-fill every field you can with a concrete proposal. A drafted answer
  the user can veto beats an open question. Suffix anything unconfirmed with
  `(draft — confirm)`.
- **Only the user can decide** priorities, scope cuts, and business numbers
  (capital, thresholds, dates, accounts). Mark those `TBD(user)` and ask via
  AskUserQuestion — at most 3–4 questions per round, most load-bearing first,
  each with your recommended option listed first.
- **The file is the source of truth, not the chat.** Write the spec file
  early and update it after every round of answers, so a session can die at
  any point without losing agreements.
- A section is *agreed* only when the user says so. When every section is
  agreed, set `status: agreed` in the front matter and update `last-updated`.
- Statuses: `draft → agreed → in-progress → done` (or `dropped`). Only the
  user moves a spec past `draft`.

## Schema rules — enforce these while drafting and reviewing

- **Goals** are outcomes, not tasks ("US sleeve rebalances monthly with costs
  on", not "edit backtest.py"). Measurable where possible. Maximum 5 — more
  means the spec should be split.
- **Non-goals** are exclusions a reasonable person might assume are in scope.
  If there are none, write "None identified" rather than deleting the section.
- **Acceptance criteria** are numbered `AC-1…`, each binary pass/fail, each
  naming its verification (a test file, a command, or an explicit manual
  check). Reject vague criteria ("works well", "is fast") — rewrite them as
  observable behaviour with a number or a named test.
- **Constraints & invariants**: always state which of `CLAUDE.md`'s
  invariants (1–6) the work touches and how each is preserved. "None touched"
  must be stated explicitly, not implied.
- **Verification plan** is exact commands (`pytest -q`,
  `python -m trading_algo.run_backtest --synthetic`, …), not prose.
- **Open questions** get IDs (`Q-1…`). When one is settled, move it to the
  Decision log with the date and who decided — don't silently delete it.

## Check mode

For each acceptance criterion in the named spec:

1. Run or inspect its stated verification (run the test/command where the
   sandbox allows; otherwise inspect the code and say so).
2. Mark the Status column: `✅ pass`, `❌ fail`, or `⚠️ unverifiable` with a
   one-line reason.
3. Update the spec file, then report the tally and any failures with evidence.

A spec with every AC ✅ and no open questions is a candidate for
`status: done` — propose it, but let the user make the call.
