#!/usr/bin/env python3
"""Compare mdvault's CJK recall with SQLite FTS5's default tokenizer.

Builds the same small vault twice — once indexed by mdvault, once by an FTS5 table using
`tokenize='porter unicode61'` (the tokenizer most vault-memory plugins use) — and runs the
same Japanese queries against both.

    python scripts/bench_cjk.py

Stdlib only. No network. Numbers are deterministic for the corpus below.

Known limit: kana↔kanji readings (にほん → 日本) need a morphological dictionary, which this
plugin deliberately does not ship. Katakana↔hiragana folds because it is a pure codepoint
mapping; kanji readings are not.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "plugins" / "mdvault"))

from vault_engine import Config, VaultIndex  # noqa: E402

NOTES = {
    "japanese/study-log.md": """# 日本語 学習ログ

今日はメモリのプラグインを作った。カタカナはまだ弱いので、毎日インプットを続ける。
一月に日本へ行く。新幹線のチケットを買う。eVisa の窓口は十月に開いた。
""",
    "japanese/flights.md": """# 日本 フライト

エミレーツ F187B5B、一月十六日から二月七日まで。成田行き、ドバイ経由。
""",
    "japanese/relocation.md": """# 移住の決定

アイルランドの Critical Skills Employment Permit を先に申請する。
ドイツはバックアップ。日本は第二の拠点。
""",
    "notes/english.md": """# Ireland decision

Apply for the Critical Skills Employment Permit first. Germany is the backup.
""",
}

# (query, note that must be retrieved)
QUERIES = [
    ("日本語", "japanese/study-log.md"),
    ("メモリ", "japanese/study-log.md"),
    ("カタカナ", "japanese/study-log.md"),
    ("新幹線", "japanese/study-log.md"),
    ("日本", "japanese/study-log.md"),
    ("フライト", "japanese/flights.md"),
    ("成田", "japanese/flights.md"),
    ("移住", "japanese/relocation.md"),
    ("アイルランド", "japanese/relocation.md"),
    ("かたかな", "japanese/study-log.md"),  # hiragana query, katakana text
]


def build_vault(root: Path) -> None:
    for rel, body in NOTES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def mdvault_hits(root: Path, query: str) -> list[str]:
    index = VaultIndex(Config(vault_path=root))
    index.refresh()
    return [hit["path"] for hit in index.search(query, limit=5)]


def fts5_hits(root: Path, db: Path, query: str) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(path UNINDEXED, body, "
            "tokenize='porter unicode61')"
        )
        if not conn.execute("SELECT count(*) FROM notes").fetchone()[0]:
            for rel, body in NOTES.items():
                conn.execute("INSERT INTO notes(path, body) VALUES (?, ?)", (rel, body))
            conn.commit()
        # A simple term query, the way a search tool would pass it through.
        rows = conn.execute(
            "SELECT path FROM notes WHERE notes MATCH ? ORDER BY rank LIMIT 5", (query,)
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "vault"
        build_vault(root)
        db = Path(tmp) / "fts5.sqlite"

        md_ok = fts_ok = 0
        print(f"{'query':<14} {'expected':<28} {'mdvault':<10} {'fts5'}")
        print("-" * 72)
        for query, expected in QUERIES:
            md = mdvault_hits(root, query)
            fts = fts5_hits(root, db, query)
            md_hit = expected in md
            fts_hit = expected in fts
            md_ok += md_hit
            fts_ok += fts_hit
            print(
                f"{query:<14} {expected:<28} "
                f"{'hit' if md_hit else 'MISS':<10} {'hit' if fts_hit else 'MISS'}"
            )
        total = len(QUERIES)
        print("-" * 72)
        print(f"found in top 5: mdvault {md_ok}/{total}   fts5 {fts_ok}/{total}")
        return 0 if md_ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
