---
name: sidequest
description: Spawn an interactive sibling Claude Code session in a new herdr/tmux pane for a side request, seeded with a distilled brief of what this session knows (or a full fork of its transcript). Use when the user says "/sidequest <request>", "spin off a side agent for X", "peel this off into its own session", "open a side session with context", or asks how a sidequest went ("sidequest report", "did the side agent finish").
---

# sidequest

Peel a slightly related request off into its own **interactive** Claude Code
session that the person can type into, without losing what this session knows.
This session keeps working; the sidequest reports back to a file.

Mechanics live in the `sidequest` CLI (`~/.local/bin/sidequest`). This skill
is about writing a good brief and choosing the mode.

## When the user says `/sidequest <request>`

1. **Pick the mode.**
   - `brief` (default): fresh session + a distilled brief you write. Cheap and
     focused. Use unless the side request needs the exact wording of the
     conversation.
   - `full`: `claude --resume <this session> --fork-session`, inheriting the
     whole transcript, plus your brief. Use when the user says "full context",
     "fork this", or the request leans on long back-and-forth detail. It starts
     as heavy as this session.
2. **Write the brief** to a scratchpad file (see template below). This is the
   whole point: the sidequest only knows what you put here. Spend the effort.
3. **Spawn:**
   ```bash
   sidequest spawn --request "<the user's request, verbatim>" \
     --brief <scratchpad>/sidequest-brief.md [--mode full] [--name <short>] \
     [--cwd <dir>] [--no-focus] [--direction down] [--ratio 0.4]
   ```
   It opens a pane to the right (focused by default, since the user is about
   to go type into it), starts Claude there with the brief as an appended
   system prompt and the request as the first message, and prints the slug.
4. **Tell the user** in two lines: the pane is open, and
   `sidequest report <slug>` (or just ask you) shows the outcome when it is done.
5. **Keep going** with whatever you were doing. Do not wait for the sidequest
   unless asked.

Not inside herdr or tmux? The CLI prints the command for the user to run in a
new terminal. Pass it along verbatim.

## Brief template

Write for a capable colleague who has read nothing. Facts, not narrative.
Identifiers exact. Under ~60 lines.

```markdown
## What the parent session is doing
One paragraph: the goal, where we are, what is in flight right now.

## Decisions already made (do not relitigate)
- ...

## Relevant files, identifiers, commands
- path/to/file.rs — why it matters
- session ids, branch names, ticket ids, URLs, env vars

## Constraints and hazards
- what must not be touched (parent owns it), what breaks easily, auth quirks

## How this request relates to the parent work
Why it was peeled off; what the parent expects back (a decision, a patch, a
finding, nothing).

## User preferences that apply here
Voice rules, UI taste, push/commit rights, anything from memory that matters.
```

Leave out sections that would be empty. Never paste the whole conversation;
that is what `--mode full` is for.

## Reading results

- `sidequest list` — every sidequest, pending vs reported.
- `sidequest report <slug>` — print the report (exit 1 if none yet).
- `sidequest wait <slug>` — block until the report file exists, then print
  it (exit 3 if the herdr agent is `blocked` waiting on the person, 1 on
  timeout). Do not use `herdr agent wait` for this: `idle` fires while Claude
  is still booting and `done` is only transient.
- `sidequest show <slug>` — metadata plus the brief that was sent.

When the user asks "how did the sidequest go", run `sidequest report` for the
most recent matching slug and summarise. If the report is missing but the
pane is still open, say so rather than guessing.

## Gotchas

- The sidequest is a real Claude Code session: same account, same config dir,
  same hooks, same permission mode as a fresh `claude` launch in that cwd.
- `--mode full` requires `CLAUDE_CODE_SESSION_ID` (set inside Claude Code) or
  `--session <id>`.
- The report path is stamped onto the first message so a forked transcript's
  older sidequest instructions cannot win. Do not strip it.
- State lives in `~/.local/state/sidequest/<slug>/` (brief.md, meta.json,
  report.md). `sidequest rm <slug>` forgets one; it never closes panes.
