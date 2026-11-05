"""Mirror built-in memory writes into a Markdown note.

Pure — no Hermes imports — so the formatting can be tested with plain pytest instead of
only through a live agent.

A mirrored note is identified by :data:`MARKER`. A same-named file without the marker is
never rewritten: the writer walks `Hermes memory.md`, `Hermes memory (2).md`, … until it
finds an owned file or a free name. That closes the adoption path where a user's own note
called `Hermes memory.md` would be silently edited (and, worse, have its bullets deleted by
a `remove`).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

MARKER = "<!-- managed by mdvault -->"
TITLE = "# Hermes memory"
HEADINGS = {"user": "## User profile", "memory": "## Agent memory"}
MAX_CANDIDATES = 20


def _initial() -> str:
    return (
        f"{TITLE}\n\n"
        f"{MARKER}\n"
        "Mirrored from Hermes's built-in memory store by the mdvault plugin. "
        "Safe to move, edit or delete.\n"
    )


def _normalise(text: str) -> str:
    return " ".join((text or "").split())


def choose_note(folder: Path) -> tuple[Path, str]:
    """Return an owned note path and its text, or a free path and ""."""
    base = folder / "Hermes memory.md"
    candidates = [base] + [folder / f"Hermes memory ({n}).md" for n in range(2, MAX_CANDIDATES + 1)]
    for candidate in candidates:
        if not candidate.exists():
            return candidate, ""
        try:
            text = candidate.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if MARKER in text:
            return candidate, text
    raise FileExistsError(
        f"no owned mirror note available in {folder} (found {MAX_CANDIDATES} foreign files)"
    )


def apply_memory_write(
    folder: Path,
    action: str,
    target: str,
    content: str,
    previous: str = "",
) -> Path | None:
    """Apply one memory write. Returns the note path, or None when nothing changed.

    ``action`` is add | replace | remove. ``remove`` legitimately carries empty
    ``content`` — the deleted entry arrives as ``previous``.
    """
    action = (action or "").strip().lower()
    if action not in ("add", "replace", "remove"):
        return None
    bullet_text = _normalise(content)
    if action in ("add", "replace") and not bullet_text:
        return None
    if action in ("replace", "remove") and not _normalise(previous) and not (action == "remove" and bullet_text):
        return None

    folder.mkdir(parents=True, exist_ok=True)
    path, text = choose_note(folder)
    if not text:
        text = _initial()
    lines = text.splitlines()
    heading = HEADINGS.get(target, HEADINGS["memory"])

    def find_heading() -> int:
        for index, line in enumerate(lines):
            if line.strip() == heading:
                return index
        return -1

    if find_heading() == -1:
        lines.extend(["", heading])
    start = find_heading()

    def section_end(start_index: int) -> int:
        for index in range(start_index + 1, len(lines)):
            if lines[index].startswith("## "):
                return index
        return len(lines)

    def remove_entry(needle: str) -> bool:
        wanted = _normalise(needle)
        if not wanted:
            return False
        end = section_end(start)
        for index in range(end - 1, start, -1):
            line = lines[index]
            if line.startswith("- ") and _normalise(line[2:]) == wanted:
                del lines[index]
                return True
        return False

    def insert_entry() -> None:
        end = section_end(start)
        insert_at = end
        while insert_at - 1 > start and not lines[insert_at - 1].startswith("- "):
            insert_at -= 1
        lines.insert(insert_at, "- " + bullet_text)

    if action == "add":
        insert_entry()
    elif action == "replace":
        remove_entry(previous)
        insert_entry()
    elif action == "remove":
        if not remove_entry(previous or content):
            return None

    write_atomic(path, "\n".join(lines) + "\n")
    return path


def write_atomic(path: Path, text: str) -> None:
    """Write via a sibling temp file + os.replace: no reader sees a half-written note."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
