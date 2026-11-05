# 90-second demo — shot list

Rules from the organisers: **under 90 seconds**, show the plugin actually working, post on X
tagging `@NousResearch`. No slides, no voice-over intro. Screen recording only.

Prepare once: `./demo/setup.sh` (creates the `mdvault-demo` profile and `~/mdvault-demo/vault`).
Terminal: large font, dark theme, no notifications. Record at 1080p.

| Time | On screen | Say (or caption) |
|---|---|---|
| 0:00–0:08 | `ls ~/mdvault-demo/vault` then `cat projects/relocation/ireland.md` | "This is my memory. Just Markdown files in a folder." |
| 0:08–0:18 | `hermes -p mdvault-demo mdvault status` | "mdvault indexes the vault — 4 notes, no database, no embeddings, no network." |
| 0:18–0:45 | `hermes -p mdvault-demo chat -q "What did I decide about Ireland, and why? One sentence."` | Let the answer land. "It never read that file in this session. The note was injected before the turn — no tool call, no prompt." |
| 0:45–1:00 | `hermes -p mdvault-demo chat -q "Remember that the Japan trip needs a Suica card."` then `cat ~/mdvault-demo/vault/inbox/*.md` | "Writing goes back into the vault — a real note with frontmatter, not a row in someone's cloud." |
| 1:00–1:15 | `hermes -p mdvault-demo chat -q "かたかなについて何を書いた?"` | "Hiragana query, katakana note — and FTS5-based memories return nothing here." |
| 1:15–1:22 | `python scripts/bench_cjk.py` (cut to the last line) | "Ten Japanese queries: 10/10 here, 2/10 on FTS5." |
| 1:15–1:30 | `hermes -p mdvault-demo mdvault search "critical skills"` + the file open in Obsidian | "It's a normal Obsidian vault. Edit it by hand, git it, sync it. The agent sees the change next turn." |

Closing card (3 s): `mdvault — your notes are the memory` + repo URL.

## Capture

```bash
# terminal only
asciinema rec --cols 100 --rows 28 demo.cast   # optional; OBS/QuickTime is fine too
```

Before recording: `clear`, close other windows, and pre-run `./demo/setup.sh` so the first frame
is instant. Do **not** show `~/.hermes/profiles` — nothing secret is there, but profile paths are
noise.

## Post copy

> Hermes plugin: mdvault. Point the agent at a folder of Markdown — Obsidian, a notes repo, a
> diary — and it recalls from it before every turn, writes decisions back as real notes, and
> indexes Japanese with bigrams. No DB, no embeddings, no network. `hermes plugins install mdvault`
> @NousResearch
