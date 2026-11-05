"""End-to-end smoke test against the real Hermes runtime.

Builds a throwaway vault + HERMES_HOME, loads the plugin exactly as Hermes does, then
drives the provider lifecycle (init → prompt block → prefetch → tools → memory mirror).

    cd ~/.hermes/hermes-agent && venv/bin/python /path/to/scripts/smoke.py
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "plugins" / "mdvault"

from plugins.plugin_loader import load_plugin_module  # noqa: E402  (needs Hermes on sys.path)

NOTES = {
    "projects/relocation/ireland.md": "---\ntags: [relocation, ireland]\n---\n"
    "# Ireland CSEP\n\nDecision: apply for the Critical Skills Employment Permit first. "
    "Software engineering is on the critical skills list. Germany is the backup plan.\n",
    "notes/insulin.md": "# Insulin protocol\n\nMorning dose with breakfast. Doctor check every "
    "three months. Watch the afternoon crash after gym.\n",
    "japanese/study-log.md": "# 日本語 学習ログ\n\n今日はメモリのプラグインを作った。"
    "カタカナはまだ弱い。\n",
    "inbox/travel.md": "# Japan flights\n\nEmirates F187B5B: 16 Jan → 7 Feb 2027, JNB to NRT via DXB.\n"
    "eVisa window opened 17 Oct 2026.\n",
    "archive/old.md": "# Stale\n\nnothing important here\n",
}

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        failures.append(label)
        print(f"  FAIL  {label} {detail}")


def main() -> int:
    logging.basicConfig(level=logging.ERROR)
    import os

    tmp = Path(tempfile.mkdtemp(prefix="mdvault-smoke-"))
    vault = tmp / "vault"
    home = tmp / "hermes_home"
    os.environ["HERMES_HOME"] = str(home)
    for rel, body in NOTES.items():
        path = vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (home / "mdvault").mkdir(parents=True)
    (home / "mdvault" / "config.json").write_text(
        json.dumps({"vault_path": str(vault), "auto_recall": "true", "write_folder": "inbox"}),
        encoding="utf-8",
    )

    module = load_plugin_module(
        "plugins.mdvault_smoke", PLUGIN_DIR, parents=(), logger=logging.getLogger("smoke")
    )
    check("plugin module loads", module is not None)
    if module is None:
        return 1

    captured = {}

    class Ctx:
        def register_memory_provider(self, provider):
            captured["provider"] = provider

    module.register(Ctx())
    provider = captured.get("provider")
    check("register() hands back a provider instance", provider is not None)
    if provider is None:
        return 1

    check("name is mdvault", provider.name == "mdvault")
    check("is_available() before init", provider.is_available())

    provider.initialize("smoke-session", hermes_home=str(home), platform="cli", agent_context="primary")
    block = provider.system_prompt_block()
    check("system prompt block names the vault", "Vault memory" in block and str(vault) in block, block)

    recall = provider.prefetch("what did I decide about Ireland and the critical skills permit")
    check("prefetch recalls the Ireland decision", "Ireland CSEP" in recall, recall[:200])
    check("recall indicator counts notes", provider.recall_status().count >= 1)

    trivial = provider.prefetch("thanks")
    check("trivial prompt skips recall", trivial == "")

    result = json.loads(provider.handle_tool_call("mdvault_search", {"query": "eVisa Japan flights"}))
    check("search finds the flights note", result["results"][0]["path"] == "inbox/travel.md", result)

    jp = json.loads(provider.handle_tool_call("mdvault_search", {"query": "カタカナ"}))
    check("Japanese query matches", jp["count"] >= 1 and jp["results"][0]["path"] == "japanese/study-log.md", jp)

    tagged = json.loads(provider.handle_tool_call("mdvault_search", {"query": "permit", "tag": "relocation"}))
    check("tag filter works", tagged["count"] == 1)

    note = json.loads(provider.handle_tool_call("mdvault_note", {"path": "notes/insulin.md"}))
    check("note read returns body", "Morning dose" in note["content"])

    recent = json.loads(provider.handle_tool_call("mdvault_recent", {"days": 30}))
    check("recent lists notes", recent["count"] == len(NOTES))

    written = json.loads(
        provider.handle_tool_call(
            "mdvault_remember",
            {"title": "Smoke decision", "content": "The plugin writes notes.", "tags": ["test"]},
        )
    )
    check("remember writes into the write folder", written.get("path") == "inbox/Smoke decision.md", written)
    check("written note on disk", (vault / "inbox" / "Smoke decision.md").is_file())

    provider.on_memory_write("add", "memory", "Kai prefers direct feedback.")
    mirror = vault / "inbox" / "Hermes memory.md"
    check("memory write mirrored", mirror.is_file() and "direct feedback" in mirror.read_text(encoding="utf-8"))
    provider.on_memory_write("replace", "memory", "Kai prefers blunt feedback.", {"previous_content": "Kai prefers direct feedback."})
    text = mirror.read_text(encoding="utf-8")
    check("replace rewrote the entry", "blunt feedback" in text and "direct feedback" not in text, text)
    provider.on_memory_write("remove", "memory", "Kai prefers blunt feedback.", {"previous_content": "Kai prefers blunt feedback."})
    text = mirror.read_text(encoding="utf-8")
    check("remove deleted the entry", "blunt feedback" not in text, text)

    provider.shutdown()
    check("shutdown is clean", True)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    print(f"workspace: {tmp}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
