"""Retrieval over local technical papers with BM25 (no model, no network, no deps).

Put PDFs (or .txt/.md) in ``papers/``. The index is cached in
``papers/.rea_index.json`` and rebuilt when files change. PDF text comes from
``pypdf`` if installed, otherwise from poppler's ``pdftotext``.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .facts import FactSheet

INDEX_NAME = ".rea_index.json"
_STOP = set(
    "a an and are as at be been but by can could did do does for from had has have how if in into is it its "
    "may more most no not of on or our out over so such than that the their them then there these they this "
    "those to under up very was we were what when where which while who will with would you your also each "
    "only other same some both all any been being per via use used using one two".split()
)


@dataclass(frozen=True)
class Passage:
    source: str
    page: int
    text: str
    score: float = 0.0
    tag: str = ""


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    out = []
    for t in toks:
        if t in _STOP or len(t) < 2:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def _pdf_pages(path: Path) -> list[tuple[int, str]]:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    except ImportError:
        pass
    if shutil.which("pdftotext") is None:
        raise RuntimeError("install pypdf (pip install pypdf) or poppler-utils to index PDFs")
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return [(i + 1, t) for i, t in enumerate(out.stdout.split("\f")) if t.strip()]


def extract_pages(path: Path) -> list[tuple[int, str]]:
    if path.suffix.lower() == ".pdf":
        return _pdf_pages(path)
    return [(1, path.read_text(encoding="utf-8", errors="replace"))]


def chunk_pages(source: str, pages: list[tuple[int, str]], words: int = 140, overlap: int = 40) -> list[Passage]:
    out = []
    for page, text in pages:
        w = re.sub(r"\s+", " ", text).strip().split(" ")
        if not w or w == [""]:
            continue
        step = max(words - overlap, 1)
        for start in range(0, len(w), step):
            piece = " ".join(w[start:start + words])
            if len(piece) > 80:
                out.append(Passage(source=source, page=page, text=piece))
            if start + words >= len(w):
                break
    return out


class Bm25Index:
    def __init__(self, passages: list[Passage], k1: float = 1.5, b: float = 0.75, files: dict | None = None):
        self.passages = passages
        self.k1, self.b = k1, b
        self.files = files or {}
        self._tf = [Counter(tokenize(p.text)) for p in passages]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df: Counter = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(passages)
        self._idf = {t: math.log(1.0 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k: int = 3) -> list[Passage]:
        q = tokenize(query)
        scored = []
        for i, tf in enumerate(self._tf):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self._len[i] / (self._avg or 1.0))
                s += self._idf.get(t, 0.0) * f * (self.k1 + 1) / denom
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        out = []
        for rank, (s, i) in enumerate(scored[:k], start=1):
            out.append(replace(self.passages[i], score=round(s, 3), tag=f"P{rank}"))
        return out

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"version": 1, "files": self.files,
                                    "passages": [asdict(p) for p in self.passages]}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Bm25Index":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls([Passage(**p) for p in data["passages"]], files=data.get("files", {}))


NOT_PAPERS = {"readme.md", "sel-papers.txt", "urls.txt"}  # notes and download lists in papers/


def _paper_files(papers_dir: Path) -> list[Path]:
    return sorted(p for p in papers_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in (".pdf", ".txt", ".md") and p.name.lower() not in NOT_PAPERS)


def index_path(papers_dir: Path) -> Path:
    """Where the index is cached: REA_INDEX_DIR if set (for read-only paper mounts), else papers/."""
    root = os.environ.get("REA_INDEX_DIR")
    return Path(root) / INDEX_NAME if root else Path(papers_dir) / INDEX_NAME


def build_index(papers_dir: Path) -> Bm25Index:
    passages: list[Passage] = []
    files = {}
    for path in _paper_files(papers_dir):
        passages += chunk_pages(path.name, extract_pages(path))
        files[path.name] = path.stat().st_mtime
    index = Bm25Index(passages, files=files)
    try:
        index.save(index_path(papers_dir))
    except OSError:
        pass  # read-only location: keep the index in memory for this run
    return index


def load_or_build(papers_dir: Path) -> Bm25Index | None:
    if not papers_dir.is_dir():
        return None
    files = {p.name: p.stat().st_mtime for p in _paper_files(papers_dir)}
    if not files:
        return None
    # An index built next to the papers (rea index-papers) is used even when the papers
    # are mounted read-only and new indexes go to REA_INDEX_DIR.
    for cache in dict.fromkeys((Path(papers_dir) / INDEX_NAME, index_path(papers_dir))):
        if cache.exists():
            try:
                idx = Bm25Index.load(cache)
                if idx.files == files:
                    return idx
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
    return build_index(papers_dir)


def query_for(sheet: FactSheet) -> str:
    """Retrieval query built from what the core found (words only, no numbers needed)."""
    parts = ["event report analysis"]
    desc = sheet.value("fault.type_description")
    if desc:
        parts.append(f"{desc} fault")
    if sheet.value("fault.ground_involved"):
        parts.append("ground fault residual current zero-sequence")
    direction = sheet.value("fault.direction")
    if direction in ("forward", "reverse"):
        parts.append(f"{direction} fault negative-sequence directional element z2 threshold")
    rot = sheet.get("assessment.rotation")
    if rot is not None and rot.priority == 1:
        parts.append("phase rotation negative-sequence positive-sequence misoperation")
    verdict = sheet.value("assessment.relay") or ""
    if "slower" in verdict or "faster" in verdict:
        parts.append("inverse-time overcurrent operating time time dial curve coordination")
    elif (sheet.value("expected.element") or "").startswith("51"):
        parts.append("inverse-time overcurrent element")
    if "failed" in verdict or "outside" in verdict or "after the fault" in verdict:
        parts.append("misoperation root cause analysis")
    breaker = sheet.value("assessment.breaker") or ""
    if breaker not in ("normal", "not applicable", ""):
        parts.append("breaker failure interrupting time clearing")
    return " ".join(parts)
