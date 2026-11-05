"""Engine tests — no Hermes runtime needed: ``pytest tests/`` works on a bare checkout."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from vault_engine import Config, VaultIndex, parse_note, slugify, tokenize, write_note


def make_vault(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def build(root: Path, **cfg) -> VaultIndex:
    index = VaultIndex(Config(vault_path=root, **cfg))
    index.refresh()
    return index


# -- tokenizer -------------------------------------------------------------


def test_tokenize_latin():
    assert tokenize("The QUICK brown foxes!") == ["quick", "brown", "foxes"]


def test_tokenize_cjk_bigrams():
    assert tokenize("日本語のノート")[:3] == ["日本", "本語", "語の"]


def test_tokenize_mixed_script():
    tokens = tokenize("Hermes メモリ is local")
    assert "hermes" in tokens and "めも" in tokens and "local" in tokens


# -- parsing ---------------------------------------------------------------


def test_frontmatter_title_and_tags():
    raw = "---\ntitle: Deploy Runbook\ntags: [ops, docker]\n---\n# Ignored\n\nBody here.\n"
    note = parse_note(raw, "ops/deploy.md", 0.0, len(raw))
    assert note.title == "Deploy Runbook"
    assert note.tags == ["ops", "docker"]
    assert "Body here." in note.text
    assert "title:" not in note.text


def test_title_falls_back_to_heading_then_filename():
    note = parse_note("# Nightly Backup\n\nText\n", "backups/nightly.md", 0.0, 10)
    assert note.title == "Nightly Backup"
    note = parse_note("just text\n", "handoff/2026-10-22.md", 0.0, 10)
    assert note.title == "2026 10 22"


def test_inline_tags_collected():
    note = parse_note("# Note\n\nworking on #relocation today\n", "n.md", 0.0, 10)
    assert "relocation" in note.tags


# -- ranking ---------------------------------------------------------------


def test_title_match_outranks_body_mention(tmp_path):
    make_vault(
        tmp_path,
        {
            "a.md": "# Insulin protocol\n\nDosing notes for the week.\n",
            "b.md": "# Shopping list\n\nmilk, eggs\n\nLater I asked about the insulin thing in passing.\n",
        },
    )
    results = build(tmp_path).search("insulin protocol")
    assert results[0]["path"] == "a.md"


def test_exact_phrase_scores(tmp_path):
    make_vault(
        tmp_path,
        {
            "phrase.md": "# Notes\n\nthe deployment pipeline runs on bun\n",
            "scatter.md": "# Notes\n\ndeployment happens elsewhere, and pipeline too\n",
        },
    )
    results = build(tmp_path).search("deployment pipeline")
    assert results[0]["path"] == "phrase.md"


def test_prefix_match(tmp_path):
    make_vault(tmp_path, {"n.md": "# Auto\n\nmemory automation notes\n"})
    assert build(tmp_path).search("automat")[0]["path"] == "n.md"


def test_tag_and_folder_filters(tmp_path):
    make_vault(
        tmp_path,
        {
            "work/a.md": "---\ntags: [kafka]\n---\nstreams design\n",
            "work/b.md": "---\ntags: [db]\n---\nstreams design\n",
            "home/c.md": "---\ntags: [kafka]\n---\nstreams design\n",
        },
    )
    index = build(tmp_path)
    tagged = [r["path"] for r in index.search("streams", tag="kafka")]
    assert set(tagged) == {"work/a.md", "home/c.md"}
    assert "work/b.md" not in tagged
    assert {r["path"] for r in index.search("streams", folder="work")} == {"work/a.md", "work/b.md"}


def test_japanese_query(tmp_path):
    make_vault(
        tmp_path,
        {
            "jp.md": "# 日本語\n\n今日はメモリのプラグインを作った。\n",
            "other.md": "# English\n\ntoday I built a plugin\n",
        },
    )
    assert build(tmp_path).search("メモリ")[0]["path"] == "jp.md"


def test_empty_query_and_empty_vault(tmp_path):
    assert build(tmp_path).search("") == []
    make_vault(tmp_path, {"n.md": "# A\n\ntext\n"})
    assert build(tmp_path).search("!!!") == []


def test_snippet_contains_match(tmp_path):
    make_vault(tmp_path, {"n.md": "# Runbook\n\n" + ("padding " * 60) + "restore the database now\n"})
    snippet = build(tmp_path).search("restore database")[0]["snippet"]
    assert "restore the database" in snippet


def test_recency_order(tmp_path):
    make_vault(tmp_path, {"old.md": "old", "new.md": "new"})
    old = tmp_path / "old.md"
    os.utime(old, (time.time() - 86400 * 30, time.time() - 86400 * 30))
    recent = build(tmp_path).recent(days=7)
    assert [n.path for n in recent] == ["new.md"]


# -- refresh ---------------------------------------------------------------


def test_incremental_refresh_picks_up_changes(tmp_path):
    make_vault(tmp_path, {"a.md": "# Alpha\n\nfirst\n"})
    index = build(tmp_path)
    assert index.search("second") == []
    (tmp_path / "a.md").write_text("# Alpha\n\nsecond revision\n", encoding="utf-8")
    index.refresh()
    assert index.search("revision")[0]["path"] == "a.md"


def test_deleted_file_drops_out(tmp_path):
    make_vault(tmp_path, {"a.md": "# Alpha\n\ntext\n"})
    index = build(tmp_path)
    (tmp_path / "a.md").unlink()
    index.refresh()
    assert index.count() == 0


def test_excluded_dirs_skipped(tmp_path):
    make_vault(tmp_path, {".obsidian/x.md": "# hidden\n", "node_modules/y.md": "# dep\n", "real.md": "# Real\n"})
    assert [n for n in build(tmp_path).notes] == ["real.md"]


def test_caps_and_stats(tmp_path):
    make_vault(tmp_path, {f"n{i}.md": f"# Note {i}\n\nbody {i}\n" for i in range(5)})
    index = build(tmp_path, max_notes=3)
    assert index.count() == 3
    assert index.stats()["notes"] == 3


# -- writing ---------------------------------------------------------------


def test_write_note_creates_frontmatter_and_never_overwrites(tmp_path):
    make_vault(tmp_path, {})
    config = Config(vault_path=tmp_path)
    first = write_note(config, "Trip plan", "Kyoto in January", tags=["japan"], folder="inbox")
    second = write_note(config, "Trip plan", "different content", tags=[], folder="inbox")
    assert first["path"] == "inbox/Trip plan.md"
    assert second["path"] == "inbox/Trip plan 2.md"
    body = (tmp_path / first["path"]).read_text(encoding="utf-8")
    assert body.startswith("---")
    assert 'title: "Trip plan"' in body
    assert '- "japan"' in body


def test_write_note_rejects_escaping_folder(tmp_path):
    config = Config(vault_path=tmp_path)
    with pytest.raises(ValueError):
        write_note(config, "x", "y", folder="../outside")


def test_write_note_keeps_existing_frontmatter(tmp_path):
    config = Config(vault_path=tmp_path)
    written = write_note(config, "raw", "---\ntags: [x]\n---\n\nbody\n", folder="")
    assert (tmp_path / written["path"]).read_text(encoding="utf-8").count("---") == 2


def test_slugify_strips_path_separators():
    assert slugify("../../etc/passwd") == "etcpasswd"
    assert slugify("") == "note"


def test_note_lookup_normalises_relative_paths(tmp_path):
    make_vault(tmp_path, {"a/b.md": "# B\n\nbody\n"})
    index = build(tmp_path)
    assert index.note("./a/b.md") is not None
    assert index.note("/a/b.md") is not None
    assert index.note("a\\b.md") is not None
    assert index.note("a/b.md") is not None


def test_skipped_counts_files_over_the_cap(tmp_path):
    make_vault(tmp_path, {f"n{i}.md": f"# Note {i}\n\nbody {i}\n" for i in range(5)})
    index = build(tmp_path, max_notes=3)
    assert index.count() == 3
    stats = index.stats()
    assert stats["truncated"] is True
    assert stats["skipped"] == 0


def test_unlimited_notes_when_cap_is_zero(tmp_path):
    make_vault(tmp_path, {f"n{i}.md": f"# Note {i}\n\nbody {i}\n" for i in range(5)})
    index = build(tmp_path, max_notes=0)
    assert index.count() == 5
    assert index.stats()["truncated"] is False


def test_oversized_file_is_skipped_and_counted(tmp_path):
    make_vault(tmp_path, {"big.md": "# Big\n\n" + ("x" * 500), "small.md": "# Small\n\ntext\n"})
    index = build(tmp_path, max_file_bytes=100)
    assert index.count() == 1
    assert index.stats()["skipped"] == 1


def test_prefix_expansion_survives_large_vocabulary(tmp_path):
    # 300 notes with distinct vocabulary: prefix credit must still resolve in one query.
    make_vault(tmp_path, {f"n{i}.md": f"# Note {i}\n\nword{i} filler{i} automate automation\n" for i in range(300)})
    results = build(tmp_path).search("automat")
    assert results and results[0]["path"].startswith("n")


def test_search_does_not_duplicate_refresh_work(tmp_path):
    make_vault(tmp_path, {"a.md": "# Alpha\n\nalpha body\n"})
    index = build(tmp_path)
    before = index.stats(refresh=False)["last_scan"]
    index.search("alpha")
    assert index.stats(refresh=False)["last_scan"] == before
