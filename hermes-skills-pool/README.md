# hermes-skills-pool

Single source of truth for hermes skills that were being duplicated as real,
independent copies across multiple hermes installs on this machine. Before
this consolidation (2026-09-22), the same skill content existed as separate
real files in up to four locations at once, so any edit to one copy silently
drifted out of sync with the others — a bug fix or update applied to one
install's copy would not propagate anywhere else, with no warning.

## What's here

Each subdirectory is the canonical, real copy of one hermes skill:

- `bruce-esp32-control/`
- `ghostesp-file-transfer/`
- `ghostesp-wifi-attack-chain/` (not authored by us — origin unknown, content
  left untouched; only the storage location changed)
- `ghostesp-wifi-recon-chain/`
- `hackrf-portapack-control/`

## Symlink structure

Every hermes install's skills directory now has a **symlink** to the
corresponding folder here, instead of a real copy:

```
/home/jack/.hermes/skills/<skill>                              -> hermes-skills-pool/<skill>
/home/jack/.hermes-offsec/skills/<skill>                        -> hermes-skills-pool/<skill>
/home/jack/.hermes-violin/skills/<skill>                        -> hermes-skills-pool/<skill>
/home/jack/.hermes-violin/profiles/violin/skills/<skill>        -> hermes-skills-pool/<skill>
```

Not every skill existed in every install before consolidation (verified with
`find` — don't assume symmetry):

- `.hermes/skills/` only ever had `bruce-esp32-control`, `ghostesp-file-transfer`,
  and `ghostesp-wifi-attack-chain`. It never had `ghostesp-wifi-recon-chain` or
  `hackrf-portapack-control`, so those two have no symlink in `.hermes/skills/`
  — this consolidation did not add skills to installs that didn't already have
  them, it only de-duplicated real files that already existed in more than one
  place.
- `.hermes-offsec/skills/` and `.hermes-violin/skills/` (both top-level and the
  profile-specific copy, see gotcha below) had all five.

Editing a skill's `SKILL.md` (or any file under it) here updates every install
at once — that's the whole point.

## IMPORTANT gotcha: hermes-violin's profile-specific skills path

`/home/jack/.hermes-violin/` has **two** skills directories:

1. `/home/jack/.hermes-violin/skills/` (top-level, looks like the obvious one)
2. `/home/jack/.hermes-violin/profiles/violin/skills/` (profile-specific)

**Only #2 is actually read** when hermes runs against this install (e.g.
`HERMES_HOME=/home/jack/.hermes-violin ... hermes -p violin skills list`, or
even the bare command with no `-p` flag, since `violin` is the only profile
under `profiles/`). This was confirmed empirically this session: temporarily
moving a skill out of the top-level `.hermes-violin/skills/` directory while
leaving `profiles/violin/skills/` untouched had **zero effect** on
`hermes skills list` output — the skill still showed as enabled, proving
`profiles/violin/skills/` is the one actually consulted.

Because of this, **both** `.hermes-violin/skills/<skill>` and
`.hermes-violin/profiles/violin/skills/<skill>` are symlinked here — the
top-level one for consistency/discoverability, and the profile-specific one
because it's the one that actually matters at runtime. If a future skill gets
added only to the top-level `.hermes-violin/skills/` and not the profile path,
hermes-violin will silently not load it. Always update (or symlink)
`profiles/violin/skills/`, not just the top-level directory.

## Verification performed (2026-09-22)

- `find` across all four skills directories confirmed exactly where each
  skill existed as a real (non-symlink) directory before touching anything.
- `diff -rq` between every pair of real copies of each skill confirmed they
  were byte-identical before consolidation (no accidental divergence was
  collapsed/lost).
- `hermes skills list` (via each install's own venv python,
  `HERMES_HOME=<install> <venv>/bin/python -m hermes_cli.main skills list`,
  and `-p violin` for the violin install) was run and diffed before and after
  the symlink conversion, for all three installs — output for these five
  skills was identical before/after.
- Live functional spot checks (not just `skills list`) via
  `-s <skill> -z "<question>"`:
  - `.hermes-offsec`, skill `bruce-esp32-control` — correctly answered a
    question about Bruce's serial settings and WebUI-only attack features.
  - `.hermes-violin` (`-p violin`), skill `hackrf-portapack-control` —
    correctly answered a question about the control script and pre-TX
    authorization check, and in doing so resolved the script path to
    `/home/jack/.hermes-violin/profiles/violin/skills/hackrf-portapack-control/scripts/portapack_control.py`,
    proving the symlink (including the nested `scripts/` subdirectory) was
    traversed correctly at runtime.

## Explicitly out of scope

`/home/jack/.claude/skills/ghostesp-file-transfer/` (Claude Code's own skill)
was **not** touched and is **not** part of this pool. It uses a different
frontmatter format (plain `name`/`description`) than hermes skills
(`name`/`description`/`trigger: []`), so the content is not interchangeable —
symlinking across that boundary would break Claude Code's skill loader.
