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

Only one external memory provider can be active at a time. Activating mdvault replaces the
built-in `MEMORY.md`/`USER.md` injection — it does not run alongside it. Built-in memory writes
can be mirrored into the vault (see `mirror_writes`), so nothing is lost.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `vault_path` | *(required)* | Folder of Markdown notes. `~` is expanded. |
| `auto_recall` | `true` | Inject the top notes into context before each turn. |
| `write_folder` | `inbox` | Vault-relative folder for notes the agent writes. |
| `prefetch_limit` | `4` | Notes injected per turn. |
| `prefetch_chars` | `1800` | Character budget for the injected block. |
| `max_notes` | `5000` | Stop indexing after this many notes. |
| `max_file_bytes` | `1000000` | Skip notes larger than this. |
| `mirror_writes` | `true` | Mirror built-in memory writes into a vault note. |
| `recency_half_life_days` | `180` | Strength of the recency tiebreak. |
| `exclude_dirs` | `.obsidian, .git, node_modules, .trash, .venv, __pycache__` | Folders never indexed. |
| `include_globs` | `*.md, *.markdown` | File suffixes indexed. |

Set them in `hermes memory setup`, in the dashboard's memory panel, under `memory.mdvault` in
`config.yaml`, or with `HERMES_VAULT_PATH` for the path (handy for a per-project vault).

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
- **Light recency tiebreak**, then path order. Same query, same corpus, same ranking — always.

The index lives in memory and is refreshed with a `stat()` sweep: only files whose mtime or size
changed are re-read, so a turn costs a few milliseconds on a vault of a few thousand notes.

The agent writes notes with YAML frontmatter (`title`, `tags`, `created`, `source: hermes`), and
notes you wrote by hand work the same way — title from frontmatter, else the first heading, else
the filename.

## Privacy and security

- **No network.** The plugin opens no sockets, fetches nothing, and has no telemetry, no
  self-updater and no background daemon.
- **Reads** only the configured vault folder (symlinks are not followed; excluded directories are
  skipped).
- **Writes** only inside that folder: new notes in `write_folder`, and the mirror note
  `<write_folder>/Hermes memory.md`. It never overwrites an existing file.
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
├── cli.py             # hermes mdvault status|search|explain|reindex
├── config_schema.py   # dashboard memory panel
├── plugin.yaml        # manifest
└── skills/vault-notes/SKILL.md
```

## License

MIT — see [LICENSE](LICENSE).
