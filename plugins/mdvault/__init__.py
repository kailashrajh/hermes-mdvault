"""mdvault — Markdown vault memory provider for Hermes Agent.

The vault *is* the memory. Point Hermes at any folder of Markdown notes (Obsidian,
Logseq, a notes repo, a diary, a docs tree) and it becomes the agent's long-term
store: searchable, editable, portable, and readable by a human without Hermes.

What it registers:

* ``prefetch`` — relevant notes from the vault are injected into context before each
  turn, so the agent recalls without being asked (``memory.mdvault.auto_recall``).
* ``mdvault_search`` / ``mdvault_note`` / ``mdvault_recent`` / ``mdvault_remember``
* ``on_memory_write`` — built-in Hermes memory writes are mirrored into the vault as a
  human-readable note, so nothing remembered is trapped inside a JSON blob.

No network, no database, no dependencies. Retrieval is BM25 with CJK bigrams,
title/heading/tag weighting and a light recency tiebreak over an in-memory index that
re-reads only files whose mtime or size changed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

from agent.memory_provider import MemoryProvider, RecallStatus, is_trivial_prompt

from .vault_engine import Config, VaultIndex, resolve_write_dir, write_note

logger = logging.getLogger(__name__)

PROVIDER_NAME = "mdvault"
CONFIG_FILE = "vault-memory.json"

_SYSTEM_BLOCK = """# Vault memory (mdvault)
Active. Notes from {vault} are recalled automatically before each turn (read-only).
- `mdvault_search` — find notes by topic, tag or folder.
- `mdvault_note` — read a full note by path.
- `mdvault_recent` — recently modified notes.
- `mdvault_remember` — write a durable note into {write_folder}/.
Search the vault before asking the user to repeat context they already wrote down."""

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "mdvault_search",
        "description": (
            "Search the user's Markdown vault (notes, diary, docs) by topic. Use for any question "
            "about past decisions, preferences, project details, people or dates the user may have "
            "written down. Returns ranked notes with path, title, tags, date and a matched snippet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in natural language."},
                "limit": {"type": "integer", "description": "Max notes to return (default 6, max 20)."},
                "folder": {"type": "string", "description": "Only search under this vault-relative folder."},
                "tag": {"type": "string", "description": "Only notes carrying this tag."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mdvault_note",
        "description": "Read one note from the vault in full, by its vault-relative path (from mdvault_search).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative path, e.g. 'projects/roadmap.md'."},
                "max_chars": {"type": "integer", "description": "Truncate the body at this many characters (default 8000)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "mdvault_recent",
        "description": "List recently modified notes in the vault — useful for 'what was I working on' questions.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look-back window in days (default 7)."},
                "limit": {"type": "integer", "description": "Max notes to return (default 10)."},
                "folder": {"type": "string", "description": "Only include notes under this vault-relative folder."},
            },
        },
    },
    {
        "name": "mdvault_remember",
        "description": (
            "Write a durable note into the vault — a decision, a preference, a fact, a meeting summary. "
            "Creates a new .md file with frontmatter; never overwrites an existing note."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title; also the file name."},
                "content": {"type": "string", "description": "Markdown body."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."},
                "folder": {"type": "string", "description": "Vault-relative folder (default: the configured inbox)."},
            },
            "required": ["title", "content"],
        },
    },
]

_SCHEMA = [
    {
        "key": "vault_path",
        "description": "Path to the folder of Markdown notes to use as memory",
        "required": True,
        "placeholder": "~/notes",
    },
    {
        "key": "auto_recall",
        "description": "Recall relevant notes automatically before every turn",
        "type": "boolean",
        "default": True,
    },
    {
        "key": "write_folder",
        "description": "Vault-relative folder for notes the agent writes",
        "default": "inbox",
    },
    {
        "key": "prefetch_limit",
        "description": "Notes injected per turn (0 disables auto-recall)",
        "type": "integer",
        "default": 4,
    },
    {
        "key": "prefetch_chars",
        "description": "Character budget for the injected recall block",
        "type": "integer",
        "default": 1800,
    },
    {
        "key": "max_notes",
        "description": "Stop indexing after this many notes (0 = no cap)",
        "type": "integer",
        "default": 5000,
    },
    {
        "key": "mirror_writes",
        "description": "Mirror built-in Hermes memory writes into a vault note",
        "type": "boolean",
        "default": True,
    },
]


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # pragma: no cover - only when the runtime is unavailable
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _sidecar_config(hermes_home: Path) -> dict[str, Any]:
    """Config written by ``hermes memory setup`` (legacy) and by the dashboard panel."""
    merged: dict[str, Any] = {}
    for path in (hermes_home / CONFIG_FILE, hermes_home / PROVIDER_NAME / "config.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def _config_yaml_block() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        memory = load_config().get("memory") or {}
        block = memory.get(PROVIDER_NAME) or {}
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def _resolve_settings(hermes_home: Path) -> dict[str, Any]:
    """Precedence, lowest to highest: sidecar/dashboard JSON → config.yaml block → env var.

    This matches ``cli._settings()``. Sidecar first means a value saved by
    ``hermes memory setup`` or the dashboard survives even when ``config.yaml`` carries
    a stale ``memory.mdvault`` block, and ``HERMES_VAULT_PATH`` wins for per-project runs.
    """
    settings: dict[str, Any] = {}
    settings.update(_sidecar_config(hermes_home))
    settings.update({k: v for k, v in _config_yaml_block().items() if v not in (None, "")})
    env_path = os.environ.get("HERMES_VAULT_PATH", "").strip()
    if env_path:
        settings["vault_path"] = env_path
    return settings


class VaultMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._config: Optional[Config] = None
        self._engine: Optional[VaultIndex] = None
        self._home: Path = _hermes_home()
        self._session_id = ""
        self._agent_context = "primary"
        self._lock = threading.Lock()
        self._last_recall = RecallStatus(provider_label="Vault", count=0)
        self._unavailable = ""

    # -- identity ---------------------------------------------------------

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def is_available(self) -> bool:
        settings = _resolve_settings(_hermes_home())
        raw = str(settings.get("vault_path") or "").strip()
        if not raw:
            return False
        return Path(raw).expanduser().is_dir()

    def unavailable_reason(self) -> str:
        return self._unavailable or (
            "Set the vault folder with `hermes memory setup` (provider: mdvault), or set "
            "HERMES_VAULT_PATH to a directory of Markdown notes."
        )

    # -- lifecycle --------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._home = Path(kwargs.get("hermes_home") or _hermes_home())
        self._session_id = session_id or ""
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        settings = _resolve_settings(self._home)
        raw_path = str(settings.get("vault_path") or "").strip()
        if not raw_path:
            self._unavailable = "No vault path configured. Run `hermes memory setup` and pick a folder of Markdown notes."
            logger.warning("mdvault: %s", self._unavailable)
            return
        vault = Path(raw_path).expanduser()
        if not vault.is_dir():
            self._unavailable = f"Vault folder does not exist: {vault}"
            logger.warning("mdvault: %s", self._unavailable)
            return
        self._config = Config.from_mapping(settings, vault)
        self._engine = VaultIndex(self._config)
        try:
            self._engine.refresh()
            logger.info("mdvault: indexed %d notes from %s", self._engine.count(), vault)
        except Exception as exc:  # keep the agent alive; tools report the error
            logger.warning("mdvault: initial index failed: %s", exc)

    def shutdown(self) -> None:
        self._engine = None
        self._config = None

    def _ensure_engine(self) -> VaultIndex:
        if self._engine is None:
            raise RuntimeError(self.unavailable_reason())
        return self._engine

    # -- prompt + recall --------------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._config:
            return ""
        try:
            count = self._engine.count() if self._engine else 0
        except Exception:
            count = 0
        if not count:
            return ""
        return _SYSTEM_BLOCK.format(
            vault=self._config.vault_path, write_folder=self._config.write_folder or "inbox"
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if (
            not self._config
            or not self._config.auto_recall
            or self._config.prefetch_limit <= 0
            or is_trivial_prompt(query)
        ):
            self._last_recall = RecallStatus(provider_label="Vault", count=0)
            return ""
        try:
            engine = self._ensure_engine()
            results = engine.search(query, limit=self._config.prefetch_limit)
            if not results:
                self._last_recall = RecallStatus(provider_label="Vault", count=0)
                return ""
            block = self._format_recall(results)
            self._last_recall = RecallStatus(provider_label="Vault", count=len(results))
            return block
        except Exception as exc:
            logger.warning("mdvault prefetch failed: %s", exc)
            self._last_recall = RecallStatus(provider_label="Vault", count=0)
            return ""

    def recall_status(self) -> Optional[RecallStatus]:
        return self._last_recall

    def _format_recall(self, results: list[dict[str, Any]]) -> str:
        budget = max(200, self._config.prefetch_chars if self._config else 1800)
        opener, closer = '<vault_memory source="mdvault">', "</vault_memory>"
        lines = [opener]
        used = len(opener) + len(closer)
        for item in results:
            tags = f" [{', '.join(item['tags'][:4])}]" if item.get("tags") else ""
            head = f"### {item['title']} ({item['path']}, {item['modified'][:10]}, {item['score']:.2f}){tags}"
            body = (item.get("snippet") or "").strip()
            remaining = budget - used
            if remaining <= len(head) + 8:
                break
            chunk = f"{head}\n{body}"
            if len(chunk) > remaining:
                # The budget applies to the FIRST note too: truncate rather than
                # overrun it, and never emit a head with no room for its body.
                chunk = chunk[: max(0, remaining - 1)].rstrip() + "…"
            lines.append(chunk)
            used += len(chunk) + 1
        lines.append(closer)
        return "\n".join(lines)

    # -- tools ------------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return list(_TOOLS)

    @staticmethod
    def _int_arg(value: Any, default: int, low: int, high: int) -> int:
        try:
            return min(max(int(float(value)), low), high)
        except (TypeError, ValueError):
            return default

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        try:
            engine = self._ensure_engine()
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        try:
            if tool_name == "mdvault_search":
                query = str(args.get("query") or "").strip()
                if not query:
                    return json.dumps({"error": "query is required"})
                limit = self._int_arg(args.get("limit"), 6, 1, 20)
                results = engine.search(
                    query,
                    limit=limit,
                    folder=args.get("folder") or None,
                    tag=args.get("tag") or None,
                )
                return json.dumps(
                    {"query": query, "count": len(results), "results": results, "stats": engine.stats(refresh=False)},
                    ensure_ascii=False,
                )
            if tool_name == "mdvault_note":
                path = str(args.get("path") or "").strip()
                note = engine.note(path)
                if note is None:
                    return json.dumps({"error": f"no note at {path!r}"})
                max_chars = self._int_arg(args.get("max_chars"), 8000, 200, 40000)
                body = note.text[:max_chars]
                return json.dumps(
                    {
                        "path": note.path,
                        "title": note.title,
                        "tags": note.tags,
                        "modified": note.to_public()["modified"],
                        "truncated": len(note.text) > len(body),
                        "content": body,
                    },
                    ensure_ascii=False,
                )
            if tool_name == "mdvault_recent":
                days = self._int_arg(args.get("days"), 7, 1, 3650)
                limit = self._int_arg(args.get("limit"), 10, 1, 50)
                notes = engine.recent(days=days, limit=limit, folder=args.get("folder") or None)
                return json.dumps(
                    {"days": days, "count": len(notes), "results": [n.to_public() for n in notes]},
                    ensure_ascii=False,
                )
            if tool_name == "mdvault_remember":
                if self._agent_context not in ("primary", ""):
                    return json.dumps({"error": "writes are skipped outside a primary session"})
                title = str(args.get("title") or "").strip()
                content = str(args.get("content") or "").strip()
                if not title or not content:
                    return json.dumps({"error": "title and content are required"})
                tags = args.get("tags") or []
                if isinstance(tags, str):
                    tags = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]
                written = write_note(
                    self._config,
                    title=title,
                    content=content,
                    tags=tags,
                    folder=args.get("folder") or None,
                )
                engine.refresh()
                return json.dumps({"ok": True, **written}, ensure_ascii=False)
            return json.dumps({"error": f"unknown tool {tool_name!r}"})
        except Exception as exc:
            logger.warning("mdvault tool %s failed: %s", tool_name, exc)
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    # -- memory mirror ----------------------------------------------------

    def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[dict[str, Any]] = None) -> None:
        """Mirror built-in memory writes into a human-readable vault note.

        ``remove`` arrives with an EMPTY ``content`` and the deleted entry in
        ``metadata["previous_content"]`` — guarding on content first silently dropped
        every removal, so a fact the user deleted stayed in the vault and could be
        recalled again. Destructive actions without identity are skipped: the ABC says
        a provider that needs exact identity must not mirror destructively without it.
        """
        if not self._config or not self._config.mirror_writes or self._agent_context not in ("primary", ""):
            return
        action = (action or "").strip().lower()
        content = (content or "").strip()
        previous = ""
        if isinstance(metadata, dict):
            previous = str(metadata.get("previous_content") or "").strip()
        if action not in ("add", "replace", "remove"):
            return
        if action == "add" and not content:
            return
        if action in ("replace", "remove") and not (previous or (action == "remove" and content)):
            logger.debug("mdvault: skipping %s mirror without entry identity", action)
            return
        try:
            self._write_mirror(action, target, content, previous)
        except Exception as exc:
            logger.warning("mdvault mirror failed: %s", exc)

    @staticmethod
    def _normalise_entry(text: str) -> str:
        return " ".join((text or "").split())

    def _write_mirror(self, action: str, target: str, content: str, previous: str) -> None:
        engine = self._ensure_engine()
        folder = resolve_write_dir(self._config, self._config.write_folder or "inbox")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "Hermes memory.md"
        marker = "<!-- managed by mdvault -->"
        heading = f"## {'User profile' if target == 'user' else 'Agent memory'}"
        bullet = "- " + self._normalise_entry(content)

        if path.exists():
            text = path.read_text(encoding="utf-8-sig")
            if marker not in text:
                # Never rewrite a note this plugin did not create.
                path = folder / "Hermes memory (mdvault).md"
                text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        else:
            text = ""

        if not text:
            text = (
                "# Hermes memory\n\n"
                f"{marker}\n"
                "Mirrored from Hermes's built-in memory store by the mdvault plugin. "
                "Safe to move, edit or delete.\n"
            )
        lines = text.splitlines()

        def find_heading() -> int:
            for index, line in enumerate(lines):
                if line.strip() == heading:
                    return index
            return -1

        if find_heading() == -1:
            lines.extend(["", heading])
        heading_index = find_heading()

        def section_bounds() -> tuple[int, int]:
            start = heading_index
            end = len(lines)
            for index in range(start + 1, len(lines)):
                if lines[index].startswith("## "):
                    end = index
                    break
            return start, end

        def remove_entry(needle: str) -> bool:
            if not needle:
                return False
            wanted = self._normalise_entry(needle)
            start, end = section_bounds()
            for index in range(end - 1, start, -1):
                line = lines[index]
                if line.startswith("- ") and self._normalise_entry(line[2:]) == wanted:
                    del lines[index]
                    return True
            return False

        def insert_entry() -> None:
            start, end = section_bounds()
            insert_at = end
            # Keep bullets contiguous at the end of their own section.
            while insert_at - 1 > start and not lines[insert_at - 1].startswith("- "):
                insert_at -= 1
            lines.insert(insert_at, bullet)

        if action == "add":
            insert_entry()
        elif action == "replace":
            remove_entry(previous)
            insert_entry()
        elif action == "remove":
            if not remove_entry(previous or content):
                return
        else:
            return

        # Atomic replace: a sync client or a second turn can never see a half-written file.
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        engine.refresh()

    # -- config -----------------------------------------------------------

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [dict(field) for field in _SCHEMA]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        home = Path(hermes_home)
        target_dir = home / PROVIDER_NAME
        target_dir.mkdir(parents=True, exist_ok=True)
        current = _sidecar_config(home)
        for key in (
            "vault_path",
            "auto_recall",
            "write_folder",
            "prefetch_limit",
            "prefetch_chars",
            "max_notes",
            "mirror_writes",
        ):
            if key in values and values[key] not in (None, ""):
                current[key] = values[key]
        # Atomic: a config read during a save must never see half a JSON document.
        tmp = target_dir / f".config.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target_dir / "config.json")

    def backup_paths(self) -> list[str]:
        # The vault lives outside HERMES_HOME on purpose: it is the user's own store and
        # should be backed up like any other document folder.
        return []

    def get_status_config(self, provider_config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Report config + index health for ``hermes memory status``.

        Called on an uninitialized instance too, so resolve the vault here rather than
        assuming ``initialize()`` ran.
        """
        config = self._config
        if config is None:
            settings = _resolve_settings(_hermes_home())
            if isinstance(provider_config, dict):
                settings = {**settings, **{k: v for k, v in provider_config.items() if v not in (None, "")}}
            raw = str(settings.get("vault_path") or "").strip()
            if not raw:
                return {"configured": False, "reason": self.unavailable_reason()}
            vault = Path(raw).expanduser()
            if not vault.is_dir():
                return {"configured": False, "reason": f"Vault folder does not exist: {vault}"}
            config = Config.from_mapping(settings, vault)
        try:
            if self._engine is not None:
                info = self._engine.stats()
            else:
                probe = VaultIndex(config)
                probe.refresh()
                info = probe.stats()
        except Exception as exc:
            info = {"error": str(exc)}
        return {
            "configured": True,
            "vault_path": str(config.vault_path),
            "auto_recall": config.auto_recall,
            "write_folder": config.write_folder,
            **info,
        }


SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def register(ctx) -> None:
    """Called by the memory plugin discovery system (one fresh instance per agent)."""
    ctx.register_memory_provider(VaultMemoryProvider())
    try:
        ctx.register_skill(
            "vault-notes",
            SKILLS_DIR / "vault-notes" / "SKILL.md",
            "Search and maintain the Markdown vault backing mdvault memory",
        )
    except Exception:  # a skill is a bonus, never a reason to fail the provider
        logger.debug("mdvault: skill registration skipped", exc_info=True)
