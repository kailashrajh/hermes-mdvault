"""CJK retrieval tests — the differentiator against FTS5-based memory providers.

SQLite FTS5's ``unicode61`` tokenizer treats a run of Japanese/Chinese/Korean characters as
one token, so a substring query returns nothing. mdvault indexes CJK as character bigrams,
folds katakana to hiragana and applies NFKC, so the same vault is searchable in either
script and either width.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from vault_engine import Config, VaultIndex, tokenize

JP = """# 日本語 学習ログ

今日はメモリのプラグインを作った。カタカナはまだ弱い。
一月に日本へ行く。新幹線のチケットを買う。
"""

KO = "# 한국어 노트\n\n오늘은 메모리 플러그인을 만들었다. 발음이 어렵다.\n"
ZH = "# 中文笔记\n\n今天做了内存插件。汉字很难。\n"
RU = "# Заметки\n\nсегодня сделал плагин памяти.\n"


@pytest.fixture
def index(tmp_path):
    for name, body in {"jp.md": JP, "ko.md": KO, "zh.md": ZH, "ru.md": RU}.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    idx = VaultIndex(Config(vault_path=tmp_path))
    idx.refresh()
    return idx


@pytest.mark.parametrize("query", ["日本語", "メモリ", "カタカナ", "新幹線", "日本"])
def test_japanese_substrings_are_retrievable(index, query):
    assert [h["path"] for h in index.search(query, limit=3)] == ["jp.md"]


def test_katakana_query_matches_hiragana_text(index):
    # かたかな does not appear literally in the note — カタカナ does.
    assert index.search("かたかな", limit=3)[0]["path"] == "jp.md"


def test_hiragana_query_matches_katakana_text(index):
    assert index.search("めもり", limit=3)[0]["path"] == "jp.md"


def test_korean_and_chinese_and_cyrillic_still_work(index):
    assert index.search("메모리", limit=3)[0]["path"] == "ko.md"
    assert index.search("内存", limit=3)[0]["path"] == "zh.md"
    assert index.search("плагин", limit=3)[0]["path"] in {"ko.md", "ru.md"}


def test_full_width_alphanumerics_match_ascii(index):
    (index.config.vault_path / "fw.md").write_text(
        "# 設定\n\nＡＰＩキーは６０秒で失効する。\n", encoding="utf-8"
    )
    index.refresh()
    assert index.search("API", limit=3)[0]["path"] == "fw.md"


def test_mixed_script_tokenizes_both_halves():
    tokens = tokenize("Docker化する")
    assert "docker" in tokens
    assert "化す" in tokens


def test_folded_tokens_are_not_written_to_disk(index):
    # Folding is index-only: the note text must keep its original katakana.
    assert "カタカナ" in (index.config.vault_path / "jp.md").read_text(encoding="utf-8")
