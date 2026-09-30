# Studio tools

Stdlib-only helpers for the coordinator and the `cinnamon-*` subagents (2026-09-23). Run with
`/usr/bin/python3 -B`. Exit codes follow the studio: 0 ok, 1 mismatch/blocked, 2 invalid request.

| Tool | Purpose | Writes |
|---|---|---|
| `design_lint.py <id>` | `design.json` schema, TODOs, 16 ANSI, fonts (`fc-list`), WCAG contrast pairs, hue census vs other presets and `~/Documents/themes/` | only with `--write` → `presets/<id>/identity/contrast.json` |
| `design.schema.json` | Schema used by `design_lint.py`; palette tokens come from `presets/_template/design.json` at runtime | — |
| `scaffold_preset.py <id>` | Copy the lineage named in `presets/_template/LINEAGE` (code + `desktop/base/` only; never state, receipts, verification, generated trees, artwork or design files) | only with `--write`; never overwrites |
| `preflight.py <id> --stage user\|root\|catalog` | Read-only go/no-go before a live stage: pending receipts, the 12 studio locks, running theme processes, `plymouthd.conf` `Theme=`, `SUDO_ASKPASS`, catalog pin `--check`, panel drift | nothing |
| `containment.py mark\|verify <id>` | Marker before delegation; verify lists live-path changes newer than it plus protected-hash drift | `mark` → `state/markers/` |
| `guard_live.py --role <role>` | Claude Code `PreToolUse` hook for subagents: blocks live commands and writes outside the role's area | nothing |

Tests: `/usr/bin/python3 -B -m unittest discover -s tools/tests -v`.

`guard_live.py` is a best-effort regex guard, not a sandbox. It backs up the prose rules in AGENTS.md;
it doesn't replace the coordinator's review.
