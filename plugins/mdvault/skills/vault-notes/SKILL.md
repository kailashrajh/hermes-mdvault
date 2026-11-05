---
name: vault-notes
description: Search and maintain the Markdown vault backing mdvault memory
---

# Vault notes

The user's long-term memory is a folder of Markdown files. It is a real folder they can open in
any editor — treat it as their document, not a database.

## Reading

- `mdvault_search` returns ranked notes with path, title, tags, date and a matched snippet.
  Search before asking the user to repeat a decision, a preference, or a project detail.
- `mdvault_note` reads one note in full when a snippet is not enough.
- `mdvault_recent` answers "what was I working on" from modification times.

Prefer two or three focused queries over one long one: the ranker weights title, heading and tag
matches, and a short query keeps those weights meaningful.

## Writing

- `mdvault_remember` creates a new note in the configured write folder (default `inbox/`).
  Use it for decisions, preferences, facts and summaries worth keeping for months.
- One note per idea. Title it like a filing label ("Ireland CSEP decision"), not like a sentence.
- Add tags for the axes someone would filter on later: project, person, year, domain.
- It never overwrites: a duplicate title gets a numeric suffix. Do not "fix" that by rewriting
  the other note — merge the content yourself and delete the duplicate with the user's consent.

## Never write

- Secrets, tokens, passwords or keys. The vault is plain text, often synced and often in git.
- Raw transcripts. Summarise. The vault is a library, not a log.
- Anything the user asked to forget — check before writing personal details back in.

## Maintenance

`hermes mdvault status` shows the path, note count and recall settings; `hermes mdvault reindex`
rebuilds the index. A missing note usually means the file is outside the vault folder, excluded
(`.obsidian`, `.git`, `node_modules`), larger than the 1 MB cap, or beyond the note cap.
