# sidequest

Spawn an **interactive** sibling Claude Code session that carries context from
the current one, in a new [herdr](https://herdr.dev) (or tmux) pane. The parent
keeps working; the sidequest reports back to a file.

Two modes:

| Mode | What the sidequest gets | Cost |
|------|-------------------------|------|
| `brief` (default) | a fresh session + a distilled brief written by the parent agent | cheap, focused |
| `full` | `claude --resume <parent> --fork-session` + the brief | exact context, heavy |

## Install

```bash
./install.sh            # copies bin/sidequest -> ~/.local/bin, skill -> ~/.claude/skills/sidequest
```

Requires Python 3.9+, Claude Code 2.1+, and herdr or tmux for automatic pane
spawning (otherwise it prints the command to run by hand).

## Use

Inside Claude Code: `/sidequest <request>`. The agent writes a brief and runs:

```bash
sidequest spawn --request "..." --brief brief.md [--mode full] [--name slug]
sidequest list
sidequest report <slug>
sidequest wait <slug>        # block until the report exists, then print it
sidequest show <slug>
sidequest rm <slug>
```

State: `~/.local/state/sidequest/<slug>/{brief.md,meta.json,report.md}`
(override with `SIDEQUEST_STATE_DIR`).

## How it works

1. Compose `brief.md` = parent context + a short protocol telling the child it
   is a sidequest and where to write its report.
2. `herdr pane split --current` → `herdr agent start <slug> --kind claude --pane <id> -- --append-system-prompt-file brief.md "<request>"`
   (retrying while the new pane's shell boots; falls back to `herdr pane run`).
   Under tmux: `tmux split-window`. Otherwise: print the command.
3. The request is stamped with the report path so a forked transcript's older
   instructions cannot redirect the report.

## Test

```bash
python3 -m unittest discover -s tests -v
```
