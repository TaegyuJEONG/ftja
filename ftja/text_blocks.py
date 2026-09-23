"""Sentence splitting + tier1 keyword block extraction.

Simplified port of JobSpyProject's text_index.py: that version carries a full
Porter-stemmer implementation to stay byte-for-byte in sync with a frontend
JS dashboard. FTJA has no frontend, so plain case-insensitive substring
matching on sentences is enough — one file, no parity burden.
"""
import hashlib
import re
from typing import List

_BULLET_RE = re.compile(r"^[•·*‣◦\-]\s*")
_NUMBER_RE = re.compile(r"^\d+[.)]\s+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> List[str]:
    if not text:
        return []
    out: List[str] = []
    for line in re.split(r"\n+", text):
        for s in _SENT_SPLIT_RE.split(line):
            s = s.strip()
            s = _BULLET_RE.sub("", s)
            s = _NUMBER_RE.sub("", s)
            s = s.strip()
            if len(s) >= 5:
                out.append(s)
    return out


def find_matched_with_context(sentences: List[str], keywords: List[str], window: int = 1) -> List[dict]:
    """Sentences that contain any keyword (case-insensitive substring), plus
    +/-window context sentences.

    Returns [{'index': int, 's_id': 'S<index>', 'sentence': str, 'matched': bool,
    'matched_keywords': [str, ...]}, ...] ordered by index — matched_keywords is
    the original-cased keyword(s) from `keywords` that hit this sentence (empty
    for context-only sentences). Empty list overall if no keyword hits (caller
    should treat that as an auto-fail with no LLM call).
    """
    if not sentences or not keywords:
        return []

    lowered = [s.lower() for s in sentences]
    kw_pairs = [(k, k.lower().strip()) for k in keywords if k.strip()]

    hits_by_index: dict = {}  # index -> [original-cased keyword, ...]
    for i, s in enumerate(lowered):
        hits = [orig for orig, low in kw_pairs if low in s]
        if hits:
            hits_by_index[i] = hits

    if not hits_by_index:
        return []

    n = len(sentences)
    expanded: dict = {}  # index -> (matched: bool, keywords: list)
    for idx in sorted(hits_by_index):
        for offset in range(-window, window + 1):
            j = idx + offset
            if 0 <= j < n:
                if offset == 0:
                    expanded[j] = (True, hits_by_index[idx])
                elif j not in expanded:
                    expanded[j] = (False, [])

    return [
        {"index": i, "s_id": f"S{i}", "sentence": sentences[i],
         "matched": expanded[i][0], "matched_keywords": expanded[i][1]}
        for i in sorted(expanded)
    ]


def blocks_for_job(description: str, tier1_keywords: List[str], window: int = 1) -> List[dict]:
    sentences = split_sentences(description or "")
    return find_matched_with_context(sentences, tier1_keywords, window)


def block_signature(blocks: List[dict]) -> str:
    """Canonical hash of a job's tier1 blocks — ported from JobSpyProject's
    text_index.t1_block_signature. Two jobs whose blocks are content-identical
    (e.g. the same JD reposted under a different LinkedIn URL) hash the same,
    so they can be grouped and judged once instead of N times.

    Measured impact in JobSpyProject was small (~2% of jobs, per project
    memory) — a real cleanup, not a major cost lever. Expect the same order
    of magnitude here; the PM-search test's 3x Siena AI repost was visible
    but not representative of the whole corpus.
    """
    if not blocks:
        return ""
    canon = "\n".join(f"{b['index']}|{int(b['matched'])}|{b['sentence']}" for b in blocks)
    return hashlib.md5(canon.encode("utf-8", "ignore")).hexdigest()
