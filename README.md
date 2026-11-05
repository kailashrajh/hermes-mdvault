# mdvault — Markdown vault memory for Hermes Agent

Your notes **are** the memory. Point Hermes at any folder of Markdown files — an Obsidian or
Logseq vault, a notes repo, a diary, a docs tree — and it becomes the agent's long-term memory:
recalled automatically before every turn, searchable with tools, and writable by the agent.

No database. No embeddings endpoint. No API key. No network calls at all. The store is plain
`.md` files you can read, edit, sync, back up and `git diff` without Hermes.

```text
you › what did I decide about Ireland?

hermes › From your notes: you decided to apply for the Critical Skills Employment
         Permit first — software engineering is on the critical skills list —
         with Germany as the backup plan. (projects/relocation/ireland.md)
```

That answer came from a file in a folder. Nothing was uploaded anywhere, and the note is still
editable in Obsidian.

## The gap it closes: Japanese, Chinese and Korean recall

Most vault memories use SQLite FTS5 with the `unicode61` tokenizer. It treats a run of CJK
characters as **one token**, so a substring query returns nothing:

| query | FTS5 `unicode61` | mdvault |
|---|---|---|
| `日本語` | 0 hits | 1 hit |
| `メモリ` | 0 hits | 1 hit |
| `新幹線` | 0 hits | 1 hit |

mdvault indexes CJK runs as **character bigrams**, folds **katakana to hiragana**, and applies
**NFKC** before tokenizing. That means:

- a `かたかな` query finds a note that only says `カタカナ` (and the reverse);
- full-width `ＡＰＩ` matches `API`;
- `Docker化する` indexes as `docker`, `化す`, `する` — the Latin word survives;
- Russian, Greek, Korean and accented Latin are tokenized too, not dropped.

Folding is index-only: snippets and the files on disk keep the original script.

Reproduce it: `python scripts/bench_cjk.py` builds one small vault and runs the same ten
queries through mdvault and through SQLite FTS5 (`porter unicode61`). Current result:

```text
recall@10: mdvault 10/10   fts5 2/10
```

FTS5's two hits are words that happen to sit alone in a heading, separated by spaces; in
running Japanese text it returns nothing.

**Known limit:** kana↔kanji readings (`にほん` → `日本`) need a morphological dictionary.
mdvault does not ship one. Katakana↔hiragana folds because it is a pure codepoint mapping.


## Install

From the Hermes plugin catalog (once listed):

```bash
hermes plugins install mdvault
hermes memory setup          # choose mdvault, point it at your vault
```

Manually, from this repository:

```bash
git clone https://github.com/kailashrajh/hermes-mdvault
cp -r hermes-mdvault/plugins/mdvault ~/.hermes/plugins/mdvault
hermes plugins enable mdvault
hermes memory setup
```

Only one external memory provider can be active at a time — but mdvault does **not** replace
Hermes's built-in `MEMORY.md`/`USER.md`; it runs *alongside* them. Hermes builds both blocks
whenever `memory.memory_enabled` / `memory.user_profile_enabled` are true (the defaults), and its
system prompt is explicitly "additive to built-in". So the built-in store keeps being written and
injected, and mdvault adds vault recall on top.

If you want the vault to be the only memory, turn the built-in store off:

```bash
hermes config set memory.memory_enabled false
hermes config set memory.user_profile_enabled false
```

With the built-ins left on, `mirror_writes` (default true) copies their writes into a vault note,
so what the agent learns in `MEMORY.md` is also browsable, greppable and diffable as Markdown.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `vault_path` | *(required)* | Folder of Markdown notes. `~` is expanded. |
| `auto_recall` | `true` | Inject the top notes into context before each turn. |
| `write_folder` | `inbox` | Vault-relative folder for notes the agent writes. |
| `prefetch_limit` | `4` | Notes injected per turn (`0` disables auto-recall). |
| `prefetch_chars` | `1800` | Character budget for the injected block, first note included. |
| `max_notes` | `5000` | Stop indexing after this many notes (`0` = no cap). |
| `mirror_writes` | `true` | Mirror built-in memory writes into a vault note. |
| `max_file_bytes` | `1000000` | Skip notes larger than this (`skipped` in status). |
| `recency_half_life_days` | `180` | Strength of the recency factor. |
| `exclude_dirs` | `.obsidian, .git, node_modules, .trash, .venv, __pycache__` | Folders never indexed and never written to. |
| `include_globs` | `*.md, *.markdown` | File suffixes indexed. |

The first seven are editable from `hermes memory setup` and the dashboard panel; the rest are
file-only settings under `memory.mdvault` in `config.yaml`.

**Precedence, lowest to highest:** `$HERMES_HOME/mdvault/config.json` (what setup and the
dashboard write) → the `memory.mdvault` block in `config.yaml` → `HERMES_VAULT_PATH`. The env var
wins so a per-shell override always takes effect; a `config.yaml` value overrides a stale panel
save.

## Tools

| Tool | What it does |
|---|---|
| `mdvault_search` | Ranked notes for a query, optionally filtered by `folder` or `tag`. Returns path, title, tags, date, score and a matched snippet. |
| `mdvault_note` | Reads one note in full by its vault-relative path. |
| `mdvault_recent` | Notes modified in the last N days. |
| `mdvault_remember` | Writes a new note with frontmatter. Never overwrites — a duplicate title gets a numeric suffix. |

CLI, active while mdvault is the selected provider:

```bash
hermes mdvault status              # path, note count, recall settings
hermes mdvault search "evisa"      # ranked search with snippets
hermes mdvault explain "evisa"     # paths and scores only
hermes mdvault reindex             # rebuild the in-memory index
```

A bundled skill (`mdvault:vault-notes`) teaches the agent the write conventions: one idea per
note, filing-label titles, tags for the axes you would filter on, and never writing secrets.

## How retrieval works

Deliberately boring, local and explainable:

- **BM25** (k1 = 1.2, b = 0.75) over note tokens, with field weighting — a title hit is worth
  more than a body hit, then headings, then tags.
- **CJK bigrams.** Japanese and Chinese runs are indexed as overlapping character bigrams, so
  `メモリ` matches `メモリのプラグイン` without a segmenter or an embedding service.
- **Phrase and pair bonuses.** The whole query appearing verbatim counts; adjacent term pairs
  count a little.
- **Prefix credit.** A query term of four or more characters matches longer words
  (`automat` → `automation`).
- **Light recency factor**, then path order. Ranking is deterministic for a given corpus and
  clock: every note in one query is scored against the same timestamp.

The index lives in memory and is refreshed with a `stat()` sweep: only files whose mtime or size
changed are re-read. A search costs roughly 12–30 ms at 2,000 notes on a 2-vCPU box (measured with
`scripts/bench.py`), a stat-only rescan ~100 ms, and `recent()` ~1 ms — fast enough to run inline
before every turn, not free.

The agent writes notes with YAML frontmatter (`title`, `tags`, `created`, `source: hermes`), and
notes you wrote by hand work the same way — title from frontmatter, else the first heading, else
the filename.

## Privacy and security

- **No network.** The plugin opens no sockets, fetches nothing, and has no telemetry, no
  self-updater and no background daemon.
- **Reads** only the configured vault folder: excluded directories are skipped, directory
  symlinks are not followed, and a **file symlink inside the vault is skipped** rather than read
  (otherwise a link to `~/.ssh/id_rsa` would be indexed).
- **Writes** are confined to the vault: a leading `/` is stripped so the folder is treated as vault-relative,
  and a folder is rejected if it contains `..`, escapes the vault, or
  names an excluded directory, and containment is checked *before* any file is created. New notes
  use `O_EXCL`, so an existing file is never replaced — not even by a racing writer. The mirror
  only rewrites a note carrying its own `<!-- managed by mdvault -->` marker; any other
  `Hermes memory.md` is left untouched and a suffixed name is used instead.
- **No secrets in the vault.** The bundled skill instructs the agent not to write credentials
  there, because a vault is usually synced and often in git.
- Plugin state (`$HERMES_HOME/mdvault/config.json`) holds the vault path and settings — nothing
  else. The vault itself is outside `$HERMES_HOME`, so `hermes backup` does not copy it: back it
  up like any other document folder.

## Limits

- **Lexical, not semantic.** It matches words, not meanings. A note that says "car" will not
  surface for "automobile". This is the price of zero dependencies and zero network; in practice,
  a well-tagged vault more than compensates.
- **Lexical storage in memory** caps at `max_notes` (5,000 by default) and 1 MB per note.
- **No PDFs, images or OCR** — Markdown only.
- **One provider at a time**, per Hermes's memory architecture.

## Development

```bash
# unit tests — run without Hermes installed
python -m pytest

# end-to-end against the real runtime (loads the plugin the way Hermes does)
cd ~/.hermes/hermes-agent && venv/bin/python /path/to/scripts/smoke.py

# the catalog's own gates
hermes plugins doctor  plugins/mdvault
hermes plugins validate plugins/mdvault --install-deps
```

Layout:

```
plugins/mdvault/
├── __init__.py        # MemoryProvider implementation + register()
├── vault_engine.py    # scanning, parsing, BM25 ranking, note writing (stdlib only)
├── mirror.py         # mirror writer for built-in memory writes (pure, no Hermes imports)
├── cli.py             # hermes mdvault status|search|explain|reindex
├── config_schema.py   # dashboard memory panel
├── mirror.py         # mirror writer for built-in memory writes (pure, no Hermes imports)
├── plugin.yaml        # manifest
└── skills/vault-notes/SKILL.md
```

## License

MIT — see [LICENSE](LICENSE).
