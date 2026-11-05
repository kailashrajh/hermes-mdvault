"""Markdown vault index and retrieval engine.

Pure standard library — no Hermes imports, so it can be unit-tested and benchmarked
without the agent runtime. A vault is any directory of Markdown files: an Obsidian
vault, a notes repo, a docs folder, a diary.

Retrieval is deliberately local and explainable:

* BM25 over note tokens (k1=1.2, b=0.75), field-weighted title/heading hits
* CJK text is indexed as character bigrams, so Japanese/Chinese notes rank correctly
  without a segmenter or an embedding service
* whole-query phrase and adjacent-bigram bonuses
* a light recency tiebreak, then path order — deterministic results every time

The index is held in memory and refreshed with a stat() sweep, so re-reads only touch
files whose mtime or size changed.
"""

from __future__ import annotations

import math
import os
import re
import time

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

INDEX_FORMAT = 1

# Word characters, or a run of CJK. \w covers Latin/digits/underscore plus CJK.
_TOKEN_RE = re.compile(r"[\w\-\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_TAG_INLINE_RE = re.compile(r"(?<![\w&])#([A-Za-z][\w/-]{1,40})")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_WS_RE = re.compile(r"\s+")

_STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have how
    i if in into is it its me my of on or our so than that the their them then there these
    they this to was we were what when where which who why will with would you your
    about after all also any because before being between both each else even ever every
    just more most much no not now only other out over own same she should some such
    too under until up very via was way well""".split()
)


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def tokenize(text: str) -> list[str]:
    """Lowercased tokens. CJK runs become overlapping character bigrams."""
    out: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if _is_cjk(raw):
            if len(raw) == 1:
                out.append(raw)
            else:
                out.extend(raw[i : i + 2] for i in range(len(raw) - 1))
            continue
        for piece in raw.split("-"):
            if len(piece) < 2 or piece in _STOPWORDS or piece.isdigit():
                continue
            out.append(piece)
    return out


def _norm_query(query: str) -> str:
    return _WS_RE.sub(" ", (query or "").strip().lower())


@dataclass
class Section:
    heading: str
    text: str


@dataclass
class Note:
    path: str  # posix, relative to the vault root
    title: str
    tags: list[str]
    mtime: float
    size: int
    sections: list[Section] = field(default_factory=list)
    text: str = ""
    tokens: Counter[str] = field(default_factory=Counter)
    length: int = 0
    tag_tokens: set[str] = field(default_factory=set)
    title_tokens: set[str] = field(default_factory=set)
    heading_tokens: set[str] = field(default_factory=set)

    def to_public(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "tags": list(self.tags),
            "modified": datetime.fromtimestamp(self.mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "words": self.length,
        }


@dataclass
class Config:
    vault_path: Path
    include_globs: tuple[str, ...] = ("*.md", "*.markdown")
    exclude_dirs: tuple[str, ...] = (".obsidian", ".git", "node_modules", ".trash", ".venv", "__pycache__")
    max_notes: int = 5000
    max_file_bytes: int = 1_000_000
    follow_symlinks: bool = False
    prefetch_limit: int = 4
    prefetch_chars: int = 1800
    search_limit: int = 6
    auto_recall: bool = True
    write_folder: str = "inbox"
    mirror_writes: bool = True
    recency_half_life_days: float = 180.0

    @classmethod
    def from_mapping(cls, values: dict[str, Any], vault_path: str | Path) -> "Config":
        cfg = cls(vault_path=Path(vault_path).expanduser())
        truthy = {"1", "true", "yes", "on", "y"}
        falsy = {"0", "false", "no", "off", "n"}
        if isinstance(values, dict):
            for key, value in values.items():
                if not hasattr(cfg, key) or key == "vault_path" or value is None:
                    continue
                default = getattr(cfg, key)
                if isinstance(default, bool):
                    if isinstance(value, str):
                        lowered = value.strip().lower()
                        if lowered in truthy:
                            setattr(cfg, key, True)
                        elif lowered in falsy:
                            setattr(cfg, key, False)
                    else:
                        setattr(cfg, key, bool(value))
                elif isinstance(default, int) and not isinstance(default, bool):
                    try:
                        setattr(cfg, key, int(float(value)))
                    except (TypeError, ValueError):
                        pass
                elif isinstance(default, float):
                    try:
                        setattr(cfg, key, float(value))
                    except (TypeError, ValueError):
                        pass
                elif isinstance(default, tuple):
                    if isinstance(value, str):
                        setattr(cfg, key, tuple(p.strip() for p in value.split(",") if p.strip()))
                    elif isinstance(value, (list, tuple)):
                        setattr(cfg, key, tuple(str(v) for v in value))
                elif isinstance(default, str) and isinstance(value, str):
                    setattr(cfg, key, value)
        return cfg


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str, int]:
    """Minimal YAML frontmatter reader: scalars and inline/bullet lists only."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, 0
    block = match.group(1)
    meta: dict[str, Any] = {}
    current_list: str | None = None
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        bullet = re.match(r"^\s*-\s*(.+?)\s*$", line)
        if bullet and current_list:
            meta.setdefault(current_list, [])
            if isinstance(meta[current_list], list):
                meta[current_list].append(bullet.group(1).strip().strip("'\""))
            continue
        kv = re.match(r"^([A-Za-z0-9_.\-]+)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key, value = kv.group(1).strip().lower(), kv.group(2).strip()
        if not value:
            meta[key] = []
            current_list = key
            continue
        current_list = None
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value.strip("'\"")
    return meta, text[match.end() :], match.end()


def parse_note(raw: str, path: str, mtime: float, size: int) -> Note:
    meta, body, _ = _parse_frontmatter(raw)
    sections: list[Section] = []
    heading = "Intro"
    buffer: list[str] = []

    def flush() -> None:
        chunk = "\n".join(buffer).strip()
        if chunk:
            sections.append(Section(heading=heading, text=chunk))

    for line in body.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush()
            heading = heading_match.group(2).strip()
            buffer = []
        else:
            buffer.append(line)
    flush()
    if not sections:
        sections = [Section(heading="Intro", text=body.strip())]

    tags_value = meta.get("tags") or meta.get("tag") or []
    if isinstance(tags_value, str):
        tags = [t.strip() for t in re.split(r"[,\s]+", tags_value) if t.strip()]
    else:
        tags = [str(t) for t in tags_value]
    for match in _TAG_INLINE_RE.finditer(body):
        tag = match.group(1).strip()
        if tag and tag not in tags:
            tags.append(tag)
    tags = tags[:32]

    title = str(meta.get("title") or "").strip()
    if not title:
        for section in sections:
            if section.heading not in ("Intro", ""):
                title = section.heading
                break
    if not title:
        title = Path(path).stem.replace("-", " ").replace("_", " ").strip().title()

    note = Note(path=path, title=title, tags=tags, mtime=mtime, size=size)
    note.sections = sections
    note.text = body.strip()
    note.tokens = Counter(tokenize(f"{title}\n{' '.join(tags)}\n{note.text}"))
    note.length = sum(note.tokens.values())
    note.title_tokens = set(tokenize(title))
    note.heading_tokens = set(tokenize(" ".join(s.heading for s in sections)))
    note.tag_tokens = {t.lower() for t in tags}
    return note


class VaultIndex:
    """In-memory index over one vault directory."""

    def __init__(self, config: Config):
        self.config = config
        self._notes: dict[str, Note] = {}
        self._df: Counter[str] = Counter()

        self._avgdl: float = 0.0
        self._dirty = True
        self._last_scan = 0.0
        self.last_scan_error: str | None = None
        self.skipped: int = 0

    # -- scanning ---------------------------------------------------------

    def _iter_files(self) -> Iterator[tuple[Path, str]]:
        root = self.config.vault_path
        for dirpath, dirnames, filenames in os.walk(root, followlinks=self.config.follow_symlinks):
            dirnames[:] = sorted(
                d for d in dirnames if d not in self.config.exclude_dirs and not d.startswith(".git")
            )
            for filename in sorted(filenames):
                if not any(filename.lower().endswith(g.lstrip("*")) for g in self.config.include_globs):
                    continue
                full = Path(dirpath) / filename
                try:
                    rel = full.relative_to(root).as_posix()
                except ValueError:
                    continue
                yield full, rel

    def refresh(self, force: bool = False) -> None:
        """Re-stat the vault and re-read only changed files."""
        root = self.config.vault_path
        if not root.is_dir():
            self.last_scan_error = f"vault not found: {root}"
            return
        seen: set[str] = set()
        added = 0
        skipped = 0
        for full, rel in self._iter_files():
            if len(seen) >= self.config.max_notes:
                skipped += 1
                continue
            try:
                stat = full.stat()
            except OSError:
                continue
            if stat.st_size > self.config.max_file_bytes:
                skipped += 1
                continue
            seen.add(rel)
            known = self._notes.get(rel)
            if known and not force and known.mtime == stat.st_mtime and known.size == stat.st_size:
                continue
            try:
                raw = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            self._notes[rel] = parse_note(raw, rel, stat.st_mtime, stat.st_size)
            added += 1
        for rel in list(self._notes):
            if rel not in seen:
                del self._notes[rel]
                added += 1
        self.skipped = skipped
        if added:
            self._rebuild_stats()
        self._dirty = False
        self._last_scan = time.time()
        self.last_scan_error = None

    def maybe_refresh(self, max_age: float = 2.0) -> None:
        if self._dirty or (time.time() - self._last_scan) > max_age:
            self.refresh()

    def _rebuild_stats(self) -> None:
        self._df = Counter()
        total = 0
        for note in self._notes.values():
            self._df.update(note.tokens.keys())
            total += note.length
        self._avgdl = (total / len(self._notes)) if self._notes else 0.0


    # -- reading ----------------------------------------------------------

    @property
    def notes(self) -> dict[str, Note]:
        return self._notes

    def count(self) -> int:
        return len(self._notes)

    def note(self, path: str) -> Note | None:
        clean = (path or "").replace("\\", "/").lstrip("/")
        while clean.startswith("./"):
            clean = clean[2:]
        return self._notes.get(clean)

    def recent(self, days: int = 7, limit: int = 10, folder: str | None = None) -> list[Note]:
        self.maybe_refresh()
        cutoff = time.time() - max(days, 1) * 86400
        rows = [
            n
            for n in self._notes.values()
            if n.mtime >= cutoff and (not folder or n.path.startswith(folder.rstrip("/") + "/"))
        ]
        rows.sort(key=lambda n: (n.mtime, n.path), reverse=True)
        return rows[: max(1, limit)]

    # -- search -----------------------------------------------------------

    def _idf(self, term: str) -> float:
        n = len(self._notes)
        df = self._df.get(term, 0)
        if n == 0:
            return 0.0
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def _score(
        self,
        note: Note,
        terms: list[str],
        phrase: str,
        phrase_pairs: set[str],
    ) -> float:
        k1, b = 1.2, 0.75
        score = 0.0
        avgdl = self._avgdl or 1.0
        length_norm = 1.0 - b + b * (note.length / avgdl)
        for term in terms:
            freq = note.tokens.get(term, 0)
            matched = freq
            if not freq and len(term) >= 4:
                # Prefix credit, scanned over THIS note's tokens: the bound is the note's
                # distinct vocabulary (hundreds), not the corpus vocabulary (10^5). Measured
                # against a precomputed bisect map on the corpus vocabulary, this is ~2x
                # faster — see scripts/bench.py.
                hits = [note.tokens[t] for t in note.tokens if t.startswith(term) and len(t) <= len(term) + 4]
                if hits:
                    matched = 0.6 * sum(hits)
            if not matched:
                continue
            idf = self._idf(term)
            score += idf * (matched * (k1 + 1)) / (matched + k1 * length_norm)
        if not score:
            return 0.0

        for term in terms:
            idf = self._idf(term)
            if term in note.title_tokens:
                score += 2.2 * idf
            if term in note.heading_tokens:
                score += 0.7 * idf
            if term in note.tag_tokens:
                score += 1.6 * idf

        if phrase and len(phrase) >= 4:
            haystack = note.text.lower()
            title_hit = phrase in note.title.lower()
            hits = haystack.count(phrase)
            if title_hit:
                score += 3.0
            score += 1.1 * min(hits, 3)

        if phrase_pairs:
            body_tokens = note.tokens
            for pair in phrase_pairs:
                left, right = pair.split(" ")
                if body_tokens.get(left) and body_tokens.get(right):
                    score += 0.35

        age_days = max(0.0, (time.time() - note.mtime) / 86400.0)
        half_life = max(1.0, self.config.recency_half_life_days)
        score *= 1.0 + 0.12 * math.exp(-age_days / half_life)
        return score

    def search(
        self,
        query: str,
        limit: int | None = None,
        folder: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        self.maybe_refresh()
        norm = _norm_query(query)
        terms = tokenize(norm)
        if not terms:
            return []
        # de-duplicate while keeping order
        seen: set[str] = set()
        ordered_terms = [t for t in terms if not (t in seen or seen.add(t))]
        phrase = norm if len(norm) >= 4 else ""
        phrase_pairs: set[str] = set()
        for left, right in zip(ordered_terms, ordered_terms[1:]):
            phrase_pairs.add(f"{left} {right}")

        folder_prefix = folder.rstrip("/") + "/" if folder else None
        tag_key = tag.lower().lstrip("#") if tag else None
        scored: list[tuple[float, Note]] = []
        for note in self._notes.values():
            if folder_prefix and not note.path.startswith(folder_prefix):
                continue
            if tag_key and tag_key not in note.tag_tokens:
                continue
            value = self._score(note, ordered_terms, phrase, phrase_pairs)
            if value > 0:
                scored.append((value, note))

        scored.sort(key=lambda item: (-item[0], -item[1].mtime, item[1].path))
        top = scored[: max(1, limit or self.config.search_limit)]
        max_score = top[0][0] if top else 0.0
        results: list[dict[str, Any]] = []
        for value, note in top:
            entry = note.to_public()
            entry["score"] = round(value / max_score, 3) if max_score else 0.0
            entry["snippet"] = self._snippet(note, ordered_terms, phrase)
            results.append(entry)
        return results

    def _snippet(self, note: Note, terms: list[str], phrase: str, width: int = 340) -> str:
        best_section: Section | None = None
        best_hits = -1
        for section in note.sections:
            lowered = section.text.lower()
            hits = sum(lowered.count(t) for t in terms)
            if phrase:
                hits += 2 * lowered.count(phrase)
            if hits > best_hits or (hits == best_hits and best_section is None):
                best_hits, best_section = hits, section
        if best_section is None:
            return ""
        text = _WS_RE.sub(" ", best_section.text).strip()
        lowered = text.lower()
        anchor = -1
        for term in terms:
            found = lowered.find(term)
            if found != -1:
                anchor = found if anchor == -1 else min(anchor, found)
        if phrase:
            found = lowered.find(phrase)
            if found != -1:
                anchor = found
        if anchor == -1:
            start = 0
        else:
            start = max(0, anchor - width // 3)
        end = min(len(text), start + width)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        if best_section.heading and best_section.heading not in ("Intro", ""):
            snippet = f"[{best_section.heading}] {snippet}"
        return snippet

    def stats(self, refresh: bool = True) -> dict[str, Any]:
        if refresh:
            self.maybe_refresh()
        newest = max((n.mtime for n in self._notes.values()), default=0)
        return {
            "vault": str(self.config.vault_path),
            "notes": len(self._notes),
            "words": sum(n.length for n in self._notes.values()),
            "skipped": self.skipped,
            "newest": datetime.fromtimestamp(newest, tz=timezone.utc).isoformat(timespec="seconds") if newest else None,
            "last_scan": datetime.fromtimestamp(self._last_scan or time.time(), tz=timezone.utc).isoformat(timespec="seconds"),
            "error": self.last_scan_error,
        }


# -- writing ---------------------------------------------------------------

_UNSAFE_NAME = re.compile(r"[^\w\s.\-()\[\]&']+", re.UNICODE)


def slugify(title: str, fallback: str = "note") -> str:
    cleaned = _UNSAFE_NAME.sub("", (title or "").strip()).strip(" .")
    cleaned = _WS_RE.sub(" ", cleaned)[:80].strip()
    return cleaned or fallback


def write_note(
    config: Config,
    title: str,
    content: str,
    tags: Iterable[str] | None = None,
    folder: str | None = None,
) -> dict[str, Any]:
    """Create a new Markdown note in the vault. Never overwrites an existing file."""
    root = config.vault_path.resolve()
    target_folder = (folder or config.write_folder or "").strip().strip("/")
    if target_folder.startswith(".."):
        raise ValueError("folder must stay inside the vault")
    directory = (root / target_folder).resolve() if target_folder else root
    if not str(directory).startswith(str(root)):
        raise ValueError("folder must stay inside the vault")
    directory.mkdir(parents=True, exist_ok=True)

    name = f"{slugify(title)}.md"
    path = directory / name
    counter = 2
    while path.exists():
        path = directory / f"{slugify(title)} {counter}.md"
        counter += 1
        if counter > 50:
            raise FileExistsError("too many notes with that title")

    body = content.strip()
    tag_list = [str(t).lstrip("#").strip() for t in (tags or []) if str(t).strip()]
    if not _FRONTMATTER_RE.match(body):
        stamp = datetime.now().strftime("%Y-%m-%d")
        header = ["---", f"title: {title.strip() or path.stem}"]
        if tag_list:
            header.append("tags: [" + ", ".join(tag_list) + "]")
        header += [f"created: {stamp}", "source: hermes", "---", ""]
        body = "\n".join(header) + body
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body + ("\n" if not body.endswith("\n") else ""), encoding="utf-8")
    os.replace(tmp, path)
    return {
        "path": path.relative_to(root).as_posix(),
        "absolute": str(path),
        "title": title.strip() or path.stem,
        "tags": tag_list,
    }
