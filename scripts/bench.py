"""Benchmark: index build + query latency on a synthetic vault.

    python scripts/bench.py [notes]

Also times the pre-optimisation prefix scan (a full token scan per note) for comparison.
"""

from __future__ import annotations

import importlib.util
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("vault_engine", ROOT / "plugins" / "mdvault" / "vault_engine.py")
engine_mod = importlib.util.module_from_spec(spec)
sys.modules["vault_engine"] = engine_mod
spec.loader.exec_module(engine_mod)

WORDS = (
    "deploy pipeline backup restore database migration incident postmortem latency cache queue "
    "scheduler budget invoice travel visa passport flight hotel rental contract offer interview "
    "insulin statin dosage gym squat bench cardio sleep mood journal therapy family mother father "
    "sister brother friend coffee music guitar novel chapter plot character review draft edit "
    "invoice tax refund bank transfer salary pension rent lease landlord plumber electrician "
    "router firmware tailscale server docker container image build release changelog"
).split()


def build_vault(root: Path, count: int, seed: int = 7) -> None:
    rng = random.Random(seed)
    for i in range(count):
        folder = rng.choice(["notes", "projects", "journal", "archive", "inbox"])
        path = root / folder / f"note-{i:05d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        tags = ", ".join(rng.sample(WORDS, 3))
        body = " ".join(rng.choices(WORDS, k=180))
        path.write_text(
            f"---\ntitle: {rng.choice(WORDS).title()} {i}\ntags: [{tags}]\n---\n"
            f"# Section {i % 12}\n\n{body}\n\n## Details\n\n{body}\n",
            encoding="utf-8",
        )


def naive_search(index, query: str, limit: int = 6):
    """The original scoring path: every note scans its own tokens for prefix hits."""
    norm = engine_mod._norm_query(query)
    terms = engine_mod.tokenize(norm)

    def score(note, terms, phrase, pairs):
        k1, b = 1.2, 0.75
        value = 0.0
        avgdl = index._avgdl or 1.0
        length_norm = 1.0 - b + b * (note.length / avgdl)
        for term in terms:
            freq = note.tokens.get(term, 0)
            matched = freq
            if not freq and len(term) >= 4:
                hits = [t for t in note.tokens if t.startswith(term) and len(t) <= len(term) + 4]
                if hits:
                    matched = 0.6 * sum(note.tokens[t] for t in hits)
            if matched:
                value += index._idf(term) * (matched * (k1 + 1)) / (matched + k1 * length_norm)
        return value

    scored = [(score(n, terms, norm, set()), n) for n in index.notes.values()]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda item: -item[0])
    return scored[:limit]


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    tmp = Path(tempfile.mkdtemp(prefix="mdvault-bench-"))
    root = tmp / "vault"
    build_vault(root, count)
    size_mb = sum(p.stat().st_size for p in root.rglob("*.md")) / 1e6
    print(f"vault: {count} notes, {size_mb:.1f} MB")

    config = engine_mod.Config(vault_path=root)
    index = engine_mod.VaultIndex(config)
    t0 = time.perf_counter()
    index.refresh()
    build_s = time.perf_counter() - t0
    print(f"index build:   {build_s * 1000:.0f} ms  ({index.count()} notes indexed)")

    t0 = time.perf_counter()
    index.refresh()
    print(f"incremental:   {(time.perf_counter() - t0) * 1000:.0f} ms  (stat-only rescan)")

    queries = ["deploy pipeline", "insulin dosage", "visa flight", "incident postmortem", "automat", "backup restore database"]
    fast, slow = [], []
    for _ in range(5):
        for q in queries:
            t0 = time.perf_counter()
            index.search(q)
            fast.append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            naive_search(index, q)
            slow.append(time.perf_counter() - t0)

    print(f"query (bisect prefix): {statistics.median(fast) * 1000:.1f} ms median, {max(fast) * 1000:.1f} ms worst")
    print(f"query (naive scan):    {statistics.median(slow) * 1000:.1f} ms median, {max(slow) * 1000:.1f} ms worst")
    print(f"speedup: {statistics.median(slow) / statistics.median(fast):.1f}x")


if __name__ == "__main__":
    main()
