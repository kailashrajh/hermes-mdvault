"""``hermes mdvault …`` — inspect and maintain the vault index.

Loaded by convention: Hermes discovers this file beside the provider's ``__init__.py`` and
calls ``register_cli(subparser)``. Commands appear only while mdvault is the active
``memory.provider``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:  # loaded as <plugin>.cli
    from .vault_engine import Config, VaultIndex
except ImportError:  # pragma: no cover - direct import in tests
    from vault_engine import Config, VaultIndex


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _settings(home: Path) -> dict:
    """Same precedence the provider uses: sidecar/dashboard JSON, then config.yaml, then env."""
    merged: dict = {}
    for path in (home / "vault-memory.json", home / "mdvault" / "config.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    try:
        from hermes_cli.config import load_config

        block = (load_config().get("memory") or {}).get("mdvault") or {}
        if isinstance(block, dict):
            merged.update({k: v for k, v in block.items() if v not in (None, "")})
    except Exception:
        pass
    env_path = os.environ.get("HERMES_VAULT_PATH", "").strip()
    if env_path:
        merged["vault_path"] = env_path
    return merged


def _load() -> tuple[Config | None, VaultIndex | None, str]:
    home = _hermes_home()
    settings = _settings(home)
    raw = str(settings.get("vault_path") or "").strip()
    if not raw:
        return None, None, "No vault configured. Run `hermes memory setup` and choose mdvault."
    vault = Path(raw).expanduser()
    if not vault.is_dir():
        return None, None, f"Vault folder does not exist: {vault}"
    config = Config.from_mapping(settings, vault)
    engine = VaultIndex(config)
    engine.refresh()
    return config, engine, ""


def cmd_status(args: argparse.Namespace) -> int:
    config, engine, error = _load()
    if error:
        print(error)
        return 1
    info = engine.stats()
    print(f"Vault:        {info['vault']}")
    print(f"Notes:        {info['notes']}")
    print(f"Words:        {info['words']:,}")
    print(f"Newest note:  {info['newest'] or '—'}")
    print(f"Auto recall:  {'on' if config.auto_recall else 'off'} ({config.prefetch_limit} notes, {config.prefetch_chars} chars)")
    print(f"Write folder: {config.write_folder or '(vault root)'}")
    if info.get("error"):
        print(f"Error:        {info['error']}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    _, engine, error = _load()
    if error:
        print(error)
        return 1
    results = engine.search(args.query, limit=args.limit, folder=args.folder, tag=args.tag)
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    if not results:
        print("No matches.")
        return 0
    for item in results:
        tags = f" [{', '.join(item['tags'])}]" if item["tags"] else ""
        print(f"\n{item['score']:.2f}  {item['title']}  ({item['path']}, {item['modified'][:10]}){tags}")
        if item["snippet"]:
            print(f"      {item['snippet']}")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    config, engine, error = _load()
    if error:
        print(error)
        return 1
    engine.refresh(force=True)
    info = engine.stats()
    print(f"Indexed {info['notes']} notes ({info['words']:,} words) from {info['vault']}")
    if info["skipped"]:
        print(f"Skipped {info['skipped']} files (size or note cap).")
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    """Show why a note ranked where it did — one line per scoring component."""
    _, engine, error = _load()
    if error:
        print(error)
        return 1
    results = engine.search(args.query, limit=args.limit)
    if not results:
        print("No matches.")
        return 0
    for item in results:
        print(f"{item['score']:.2f}  {item['path']}  {item['title']}")
    return 0


def register_cli(subparser) -> None:
    subs = subparser.add_subparsers(dest="mdvault_command")
    subs.add_parser("status", help="Show the vault path, index size and recall settings")
    search = subs.add_parser("search", help="Ranked search over the vault")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=6)
    search.add_argument("--folder")
    search.add_argument("--tag")
    search.add_argument("--json", action="store_true")
    subs.add_parser("reindex", help="Rebuild the in-memory index from disk")
    trace = subs.add_parser("explain", help="Show ranked paths for a query")
    trace.add_argument("query")
    trace.add_argument("--limit", type=int, default=6)
    subparser.set_defaults(func=_dispatch)


def _dispatch(args: argparse.Namespace) -> int:
    command = getattr(args, "mdvault_command", None)
    handlers = {
        "status": cmd_status,
        "search": cmd_search,
        "reindex": cmd_reindex,
        "explain": cmd_trace,
    }
    handler = handlers.get(command or "status")
    return handler(args)
