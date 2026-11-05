#!/usr/bin/env bash
# Build a throwaway demo environment for the 90-second hackathon video:
#   profile "mdvault-demo"  +  plugin installed  +  a small sample vault.
#
#   ./demo/setup.sh            # create everything
#   ./demo/setup.sh --clean    # remove the profile and the sample vault
set -euo pipefail

PROFILE="${PROFILE:-mdvault-demo}"
VAULT="${VAULT:-$HOME/mdvault-demo/vault}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Always target the real home: a stray HERMES_HOME (from a test shell) would build the
# demo profile somewhere invisible.
unset HERMES_HOME

clean() {
  if [[ -f "$VAULT/.mdvault-demo" ]]; then
    rm -rf "$VAULT"
    echo "removed demo vault $VAULT"
  else
    echo "refusing to delete $VAULT (not created by this script)"
  fi
  local home="${HERMES_HOME:-$HOME/.hermes}"
  if [[ -f "$home/profiles/$PROFILE/.mdvault-demo" ]]; then
    rm -rf "$home/profiles/$PROFILE" "$HOME/.local/bin/$PROFILE"
    echo "removed demo profile $PROFILE"
  else
    echo "refusing to delete profile $PROFILE (not created by this script)"
  fi
}

if [[ "${1:-}" == "--clean" ]]; then
  clean
  exit 0
fi

mkdir -p "$VAULT"/{projects/relocation,notes,japanese,inbox}

cat >"$VAULT/projects/relocation/ireland.md" <<'EOF'
---
title: Ireland CSEP decision
tags: [relocation, ireland, career]
---
# Ireland CSEP decision

Decided 2026-10-30: apply for the **Critical Skills Employment Permit** first.
Software engineering is on Ireland's critical skills list, and dual citizenship
is kept. Germany is the backup. The Netherlands is excluded — renunciation
requirement. Japan stays as a second base with annual visits.
EOF

cat >"$VAULT/notes/insulin.md" <<'EOF'
---
title: Insulin resistance protocol
tags: [health]
---
# Insulin resistance protocol

Morning dose with breakfast, three-monthly bloods. The afternoon crash after
the gym is the pattern to watch. Statin taken at night.
EOF

cat >"$VAULT/japanese/study-log.md" <<'EOF'
---
title: 日本語 学習ログ
tags: [japanese, study]
---
# 日本語 学習ログ

今日はメモリのプラグインを作った。カタカナはまだ弱いので、毎日アニキの
インプットを続ける。一月に日本へ行く。
EOF

cat >"$VAULT/inbox/flights.md" <<'EOF'
---
title: Japan flights
tags: [japan, travel]
---
# Japan flights

Emirates F187B5B — 16 Jan → 7 Feb 2027, JNB ⇄ NRT via DXB.
eVisa window opened 17 Oct 2026. Checklist is in the palace.
EOF

mkdir -p "$VAULT/.obsidian"
echo "this file must never appear in results" >"$VAULT/.obsidian/workspace.md"
# Ownership marker: --clean only removes what this script created.
date -u +%Y-%m-%dT%H:%M:%SZ >"$VAULT/.mdvault-demo"

PROFILE_CREATED=0
if ! hermes profile show "$PROFILE" >/dev/null 2>&1; then
  hermes profile create "$PROFILE" --clone
  PROFILE_CREATED=1
fi

HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_DIR/profiles/$PROFILE/plugins/mdvault"
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -r "$REPO/plugins/mdvault" "$DEST"
if [[ "$PROFILE_CREATED" == "1" ]]; then
  date -u +%Y-%m-%dT%H:%M:%SZ >"$HERMES_DIR/profiles/$PROFILE/.mdvault-demo"
fi

hermes -p "$PROFILE" plugins enable mdvault >/dev/null
hermes -p "$PROFILE" config set memory.provider mdvault >/dev/null
hermes -p "$PROFILE" config set memory.mdvault.vault_path "$VAULT" >/dev/null 2>&1 || true
# Not served by the root multiplexed gateway (which serves the main profile): let this
# profile run its own local agent instead of refusing with profile_mismatch.
hermes -p "$PROFILE" config set gateway.standalone true >/dev/null 2>&1 || true

echo
echo "demo profile: $PROFILE"
echo "vault:        $VAULT"
echo
echo "on camera:"
echo "  hermes -p $PROFILE mdvault status"
echo "  hermes -p $PROFILE chat -q 'what did I decide about Ireland, and why?'"
