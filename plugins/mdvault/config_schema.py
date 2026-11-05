"""Declarative config surface for the mdvault memory provider.

Loaded by path by the dashboard's memory-provider router (never imported as a package),
so it may only import the pure-data schema module. Values are stored in
``$HERMES_HOME/mdvault/config.json``.
"""

from plugins.memory.config_schema import (
    KIND_BOOL,
    KIND_NUMBER,
    KIND_TEXT,
    STORAGE_FLAT_JSON,
    ProviderConfigSchema,
    ProviderField,
)

CONFIG_SCHEMA = ProviderConfigSchema(
    name="mdvault",
    label="Markdown vault",
    storage=STORAGE_FLAT_JSON,
    docs_url="https://github.com/kailashrajh/hermes-mdvault#configuration",
    fields=(
        ProviderField(
            key="vault_path",
            label="Vault folder",
            kind=KIND_TEXT,
            placeholder="~/notes",
            description="Folder of Markdown notes to use as memory.",
            info=(
                "Any directory of .md files: an Obsidian or Logseq vault, a notes repo, a docs "
                "tree. Subfolders are searched recursively; .obsidian/.git/node_modules are skipped."
            ),
            inline=True,
        ),
        ProviderField(
            key="auto_recall",
            label="Recall before every turn",
            kind=KIND_BOOL,
            default="true",
            description="Inject the most relevant notes into context automatically.",
            inline=True,
        ),
        ProviderField(
            key="write_folder",
            label="Write folder",
            kind=KIND_TEXT,
            default="inbox",
            description="Vault-relative folder for notes the agent writes.",
            group="Writing",
        ),
        ProviderField(
            key="prefetch_limit",
            label="Notes per turn",
            kind=KIND_NUMBER,
            default="4",
            description="How many notes auto-recall injects.",
            group="Advanced",
        ),
        ProviderField(
            key="prefetch_chars",
            label="Recall budget (chars)",
            kind=KIND_NUMBER,
            default="1800",
            description="Upper bound on the size of the auto-recall block.",
            group="Advanced",
        ),
        ProviderField(
            key="max_notes",
            label="Index cap",
            kind=KIND_NUMBER,
            default="5000",
            description="Stop indexing after this many notes.",
            group="Advanced",
        ),
        ProviderField(
            key="mirror_writes",
            label="Mirror Hermes memory writes",
            kind=KIND_BOOL,
            default="true",
            description="Copy built-in memory adds/edits/removals into a vault note.",
            group="Advanced",
        ),
    ),
)
