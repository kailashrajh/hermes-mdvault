"""Benchmark the vault index: build cost, incremental rescan, query latency, memory.

    python scripts/bench.py [notes]      # default 2000

Numbers from this box (2 vCPU, 3.8 GB) at 2,000 notes / 5.5 MB: build ~1.8 s,
incremental rescan ~70 ms, median query ~12 ms. Prefix matching is scanned per note
(the note's own vocabulary, not the corpus vocabulary) — a precomputed corpus-wide
bisect map measured ~2x SLOWER, which is why the simple loop stayed.
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
    print(f"index build:    {(time.perf_counter() - t0) * 1000:.0f} ms ({index.count()} notes)")

    t0 = time.perf_counter()
    index.refresh()
    print(f"rescan (stat):  {(time.perf_counter() - t0) * 1000:.0f} ms")

    tokens = sum(len(n.tokens) for n in index.notes.values())
    print(f"in-memory:      {tokens} token slots across {index.count()} notes")

    queries = [
        "deploy pipeline",
        "insulin dosage",
        "visa flight",
        "incident postmortem",
        "automat",
        "backup restore database",
        "guitar novel",
    ]
    timings = []
    for _ in range(10):
        for query in queries:
            t0 = time.perf_counter()
            index.search(query)
            timings.append(time.perf_counter() - t0)
    timings.sort()
    median = statistics.median(timings) * 1000
    p95 = timings[int(len(timings) * 0.95) - 1] * 1000
    print(f"search:         {median:.1f} ms median, {p95:.1f} ms p95 ({len(timings)} queries)")

    t0 = time.perf_counter()
    index.recent(days=7, limit=10)
    print(f"recent():       {(time.perf_counter() - t0) * 1000:.1f} ms")


if __name__ == "__main__":
    main()
