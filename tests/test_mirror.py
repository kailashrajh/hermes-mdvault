"""Mirror tests — the part that used to be covered only by a live smoke script."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mirror import MARKER, apply_memory_write, choose_note


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_first_write_creates_a_marked_note(tmp_path):
    path = apply_memory_write(tmp_path, "add", "memory", "Kai prefers direct feedback.")
    assert path is not None
    text = read(path)
    assert MARKER in text
    assert "direct feedback" in text
    assert "## Agent memory" in text


def test_user_and_memory_entries_land_under_their_own_headings(tmp_path):
    apply_memory_write(tmp_path, "add", "memory", "Agent fact.")
    apply_memory_write(tmp_path, "add", "user", "User fact.")
    path = apply_memory_write(tmp_path, "add", "memory", "Second agent fact.")
    lines = read(path).splitlines()
    agent_at = lines.index("## Agent memory")
    user_at = lines.index("## User profile")
    assert agent_at < user_at
    # Second agent fact stays in the agent section, not appended at EOF.
    assert "Second agent fact." in "\n".join(lines[agent_at:user_at])
    assert "User fact." in "\n".join(lines[user_at:])


def test_replace_rewrites_in_place_and_keeps_section(tmp_path):
    path = apply_memory_write(tmp_path, "add", "user", "likes verbose answers")
    apply_memory_write(tmp_path, "replace", "user", "likes short answers", "likes verbose answers")
    text = read(path)
    assert "likes short answers" in text
    assert "likes verbose answers" not in text
    lines = text.splitlines()
    assert "- likes short answers" in lines[lines.index("## User profile"):]


def test_remove_with_empty_content_uses_previous(tmp_path):
    path = apply_memory_write(tmp_path, "add", "memory", "temporary fact")
    result = apply_memory_write(tmp_path, "remove", "memory", "", "temporary fact")
    assert result == path
    assert "temporary fact" not in read(path)


def test_remove_without_identity_is_refused(tmp_path):
    apply_memory_write(tmp_path, "add", "memory", "keep me")
    assert apply_memory_write(tmp_path, "remove", "memory", "", "") is None
    assert apply_memory_write(tmp_path, "replace", "memory", "new", "") is None


def test_duplicate_bullets_remove_one_only(tmp_path):
    path = apply_memory_write(tmp_path, "add", "memory", "same")
    apply_memory_write(tmp_path, "add", "memory", "same")
    apply_memory_write(tmp_path, "remove", "memory", "", "same")
    assert read(path).count("- same") == 1


def test_foreign_note_is_never_adopted(tmp_path):
    foreign = tmp_path / "Hermes memory.md"
    foreign.write_text("# My own notes\n\n- do not touch\n", encoding="utf-8")
    path = apply_memory_write(tmp_path, "add", "memory", "agent fact")
    assert path is not None and path.name == "Hermes memory (2).md"
    assert read(foreign) == "# My own notes\n\n- do not touch\n"


def test_second_foreign_name_also_skipped(tmp_path):
    (tmp_path / "Hermes memory.md").write_text("mine", encoding="utf-8")
    (tmp_path / "Hermes memory (2).md").write_text("also mine", encoding="utf-8")
    path = apply_memory_write(tmp_path, "add", "memory", "agent fact")
    assert path is not None and path.name == "Hermes memory (3).md"
    assert read(tmp_path / "Hermes memory (2).md") == "also mine"


def test_owned_note_is_reused_after_foreign_collision(tmp_path):
    (tmp_path / "Hermes memory.md").write_text("mine", encoding="utf-8")
    first = apply_memory_write(tmp_path, "add", "memory", "one")
    second = apply_memory_write(tmp_path, "add", "memory", "two")
    assert first == second
    assert read(first).count(MARKER) == 1


def test_choose_note_reports_when_every_name_is_taken(tmp_path):
    for index in range(20):
        name = "Hermes memory.md" if index == 0 else f"Hermes memory ({index + 1}).md"
        (tmp_path / name).write_text("foreign", encoding="utf-8")
    with pytest.raises(FileExistsError):
        choose_note(tmp_path)


def test_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    apply_memory_write(tmp_path, "add", "memory", "one")
    apply_memory_write(tmp_path, "add", "memory", "two")
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_unknown_action_is_ignored(tmp_path):
    assert apply_memory_write(tmp_path, "frobnicate", "memory", "x") is None
    assert list(tmp_path.iterdir()) == []


def test_content_whitespace_is_normalised(tmp_path):
    path = apply_memory_write(tmp_path, "add", "memory", "  two   spaces\nand a newline ")
    assert "- two spaces and a newline" in read(path)
