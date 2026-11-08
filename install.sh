#!/usr/bin/env bash
# Install the sidequest CLI and Claude Code skill. Copies (never symlinks) the
# skill so the shared ~/.claude/skills dir stays plain files.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${SIDEQUEST_BIN_DIR:-$HOME/.local/bin}"
skills_dir="${SIDEQUEST_SKILLS_DIR:-$HOME/.claude/skills}"

mkdir -p "$bin_dir" "$skills_dir/sidequest"
install -m 0755 "$here/bin/sidequest" "$bin_dir/sidequest"
cp "$here/skill/SKILL.md" "$skills_dir/sidequest/SKILL.md"

echo "installed: $bin_dir/sidequest ($("$bin_dir/sidequest" --version))"
echo "installed: $skills_dir/sidequest/SKILL.md"
case ":$PATH:" in *":$bin_dir:"*) ;; *) echo "note: $bin_dir is not on PATH";; esac
