"""Regression tests for defects found in review.

Each test reproduces an actual attack or failure observed against the plugin, so a
future refactor cannot silently reintroduce it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from vault_engine import Config, VaultWriteError, VaultIndex, parse_note, resolve_write_dir, tokenize, write_note


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


# -- filesystem containment ------------------------------------------------


@pytest.mark.parametrize("folder", ["../escape", "x/../../escape", "./../escape", "a/../../../escape"])
def test_write_folder_cannot_leave_the_vault(tmp_path, folder):
    vault = make_vault(tmp_path / "vault", {})
    config = Config(vault_path=vault)
    with pytest.raises(VaultWriteError):
        write_note(config, "pwn", "exfiltrated", folder=folder)
    # Nothing may be created outside the vault, not even before the error.
    assert not (tmp_path / "escape").exists()
    assert not (tmp_path.parent / "escape").exists()


def test_absolute_write_folder_is_confined(tmp_path):
    vault = make_vault(tmp_path / "vault", {})
    config = Config(vault_path=vault)
    outside = tmp_path / "outside"
    result = write_note(config, "note", "body", folder=str(outside))
    # An absolute path is treated as vault-relative, never as an escape.
    assert (vault / result["path"]).is_file()
    assert not outside.exists()


@pytest.mark.parametrize("folder", [".git", ".git/hooks", ".obsidian", "notes/.trash"])
def test_excluded_directories_refuse_writes(tmp_path, folder):
    vault = make_vault(tmp_path / "vault", {})
    config = Config(vault_path=vault)
    with pytest.raises(VaultWriteError):
        write_note(config, "hook", "x", folder=folder)


def test_mirror_folder_is_confined_too(tmp_path):
    vault = make_vault(tmp_path / "vault", {})
    config = Config(vault_path=vault, write_folder="../mirror-escape")
    with pytest.raises(VaultWriteError):
        resolve_write_dir(config, None)


def test_symlinked_files_are_not_indexed(tmp_path):
    secrets = tmp_path / "id_rsa"
    secrets.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n", encoding="utf-8")
    vault = make_vault(tmp_path / "vault", {"real.md": "# Real\n\nordinary note\n"})
    os.symlink(secrets, vault / "linked.md")
    index = build(vault)
    assert index.count() == 1
    assert index.search("OPENSSH") == []


# -- tokenizer -------------------------------------------------------------


def test_mixed_script_keeps_latin_words():
    tokens = tokenize("Docker化する方法")
    assert "docker" in tokens
    assert "化す" in tokens


def test_pure_cjk_still_bigrams():
    tokens = tokenize("メモリ")
    assert tokens == ["めも", "もり"]  # katakana folded to hiragana on purpose


def test_single_kanji_query_matches(tmp_path):
    make_vault(tmp_path, {"jp.md": "# 猫\n\n猫が好きです。\n"})
    assert build(tmp_path).search("猫")[0]["path"] == "jp.md"


def test_url_fragment_is_not_a_tag(tmp_path):
    note = parse_note("# Note\n\nsee https://example.com/page#anchor for details\n", "n.md", 0.0, 10)
    assert "anchor" not in note.tags


# -- parsing ---------------------------------------------------------------


def test_utf8_bom_does_not_break_frontmatter(tmp_path):
    path = tmp_path / "bom.md"
    path.write_text("\ufeff---\ntitle: Real Title\n---\n\nbody\n", encoding="utf-8")
    index = build(tmp_path)
    note = index.note("bom.md")
    assert note is not None
    assert note.title == "Real Title"


# -- writing ---------------------------------------------------------------


def test_yaml_special_characters_in_title_and_tags(tmp_path):
    vault = make_vault(tmp_path / "vault", {})
    config = Config(vault_path=vault)
    result = write_note(
        config,
        'Decision: use [mdvault], "quoted"',
        "body",
        tags=["a,b", "c]d"],
        folder="inbox",
    )
    text = (vault / result["path"]).read_text(encoding="utf-8")
    # Round-trips through our own frontmatter reader, which is what indexes it.
    note = parse_note(text, result["path"], 0.0, len(text))
    assert note.title.startswith("Decision: use [mdvault]")
    assert "a,b" in note.tags


def test_title_newlines_cannot_inject_frontmatter(tmp_path):
    vault = make_vault(tmp_path / "vault", {})
    config = Config(vault_path=vault)
    result = write_note(config, "Bad\ntitle: injected", "body", folder="inbox")
    text = (vault / result["path"]).read_text(encoding="utf-8")
    title_lines = [line for line in text.splitlines() if line.startswith("title:")]
    assert len(title_lines) == 1
    # The newline was folded into the title value instead of injecting a second key.
    assert "injected" in parse_note(text, result["path"], 0.0, 10).title


def test_competing_writer_is_never_overwritten(tmp_path, monkeypatch):
    vault = make_vault(tmp_path / "vault", {})
    config = Config(vault_path=vault)
    inbox = vault / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    # Simulate a sync client winning the race between name choice and creation.
    (inbox / "Race.md").write_text("someone else's note", encoding="utf-8")
    result = write_note(config, "Race", "mine", folder="inbox")
    assert result["path"] == "inbox/Race 2.md"
    assert (inbox / "Race.md").read_text(encoding="utf-8") == "someone else's note"


# -- caps and budgets ------------------------------------------------------


def test_zero_prefetch_limit_is_respected_by_engine_limits(tmp_path):
    make_vault(tmp_path, {"a.md": "# A\n\nalpha\n"})
    index = build(tmp_path)
    # The provider skips prefetch at limit 0; search() still needs a sane floor.
    assert len(index.search("alpha", limit=1)) == 1
    assert index.stats()["notes"] == 1


def test_recent_ties_sort_by_path_ascending(tmp_path):
    make_vault(tmp_path, {"c.md": "# C\n", "a.md": "# A\n", "b.md": "# B\n"})
    stamp = 1_700_000_000
    for name in ("c.md", "a.md", "b.md"):
        os.utime(tmp_path / name, (stamp, stamp))
    assert [n.path for n in build(tmp_path).recent(days=9999)] == ["a.md", "b.md", "c.md"]


def test_recency_is_consistent_within_one_query(tmp_path):
    make_vault(tmp_path, {"a.md": "# A\n\nalpha body\n", "b.md": "# B\n\nalpha body\n"})
    index = build(tmp_path)
    first = [r["raw_score"] for r in index.search("alpha")]
    second = [r["raw_score"] for r in index.search("alpha")]
    assert first == second
