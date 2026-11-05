"""Provider-level mirror tests.

These run without a full Hermes checkout: ``agent.memory_provider`` is stubbed so the
plugin module can be imported. They lock in the mirror guarantees that the engine-level
tests in test_mirror.py cannot reach (containment via resolve_write_dir, on_memory_write
routing of removals through metadata['previous_content']).
"""

from __future__ import annotations

import importlib.util
import sys
import types
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugins" / "mdvault"


def _load_provider():
    # Stub the one Hermes module the plugin imports at module load.
    if "agent" not in sys.modules:
        sys.modules["agent"] = types.ModuleType("agent")
    if "agent.memory_provider" not in sys.modules:
        mod = types.ModuleType("agent.memory_provider")

        class MemoryProvider:  # minimal base
            def __init__(self):
                pass

        class RecallStatus:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        def is_trivial_prompt(text):
            return False

        mod.MemoryProvider = MemoryProvider
        mod.RecallStatus = RecallStatus
        mod.is_trivial_prompt = is_trivial_prompt
        sys.modules["agent.memory_provider"] = mod

    # Stub hermes_constants (used by some plugins) defensively.
    if str(REPO / "plugins") not in sys.path:
        sys.path.insert(0, str(REPO / "plugins"))
    # Force a fresh import.
    for name in list(sys.modules):
        if name == "mdvault" or name.startswith("mdvault."):
            del sys.modules[name]
    import mdvault as module  # noqa: E402
    return module


@pytest.fixture
def provider(tmp_path):
    module = _load_provider()
    cfg = {"vault_path": str(tmp_path / "vault"), "write_folder": "inbox", "mirror_writes": True}
    (tmp_path / "vault" / "inbox").mkdir(parents=True)
    p = module.VaultMemoryProvider()
    p._config = module.Config(vault_path=Path(cfg["vault_path"]), write_folder="inbox", mirror_writes=True)
    p._engine = module.VaultIndex(p._config)
    return p, module


def test_remove_routes_through_previous_content(provider):
    p, m = provider
    # seed a note via the mirror
    p._write_mirror("add", "memory", "to be removed", "")
    assert "to be removed" in (p._config.vault_path / "inbox" / "Hermes memory.md").read_text()
    p._write_mirror("remove", "memory", "", "to be removed")
    text = (p._config.vault_path / "inbox" / "Hermes memory.md").read_text()
    assert "to be removed" not in text


def test_on_memory_write_remove_with_empty_content(provider):
    p, m = provider
    p._write_mirror("add", "memory", "entry a", "")
    p.on_memory_write("remove", "memory", "", {"previous_content": "entry a"})
    text = (p._config.vault_path / "inbox" / "Hermes memory.md").read_text()
    assert "entry a" not in text


def test_foreign_mirror_note_is_never_adopted(provider):
    p, m = provider
    inbox = p._config.vault_path / "inbox"
    (inbox / "Hermes memory.md").write_text("# Mine\n\n- precious\n", encoding="utf-8")
    (inbox / "Hermes memory (2).md").write_text("# Also mine\n\n- keep\n", encoding="utf-8")
    p._write_mirror("add", "memory", "new entry", "")
    # both foreign files untouched
    assert "precious" in (inbox / "Hermes memory.md").read_text()
    assert "keep" in (inbox / "Hermes memory (2).md").read_text()
    # a new owned file was created
    owned = [f for f in inbox.iterdir() if f.name.startswith("Hermes memory") and "mdvault" in f.read_text()[:200] if f.is_file()]
    assert owned, "no owned mirror note created"


def test_mirror_rejects_symlinked_note(provider, tmp_path):
    p, m = provider
    inbox = p._config.vault_path / "inbox"
    external = tmp_path / "external.md"
    external.write_text("# external\n", encoding="utf-8")
    link = inbox / "Hermes memory.md"
    link.symlink_to(external)
    p._write_mirror("add", "memory", "should not land", "")
    # external file must be untouched
    assert external.read_text() == "# external\n"
    # and no symlink was followed to write into it: the external file is unchanged
    assert "should not land" not in external.read_text()
