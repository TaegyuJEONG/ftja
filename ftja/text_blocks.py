"""Sentence splitting + tier1 keyword block extraction.

Ported from JobSpyProject's text_index.py: sentences are tokenized and
Porter-stemmed, and a keyword matches when its stemmed tokens appear as a
run of whole tokens in the sentence. "work" matches "working" but not
"framework"; "time zone" matches "time zones"; "on-site" matches "on site".

Three things differ from that port, because FTJA keywords are whatever the
user types and JDs are not only English:
  - tokens are Unicode words, so "télétravail" is one token
  - a keyword written in capitals ("UTC", "CET") only matches the same
    capitals, so "CET" is not the French word "cet"
  - a keyword with a symbol in it ("100%", "C++") is matched literally

Sentences are cleaned of the scraper's markdown (backslash escapes, `**bold**`,
heading and bullet marks) before anything else, in the manner of JobSpyProject's
job_evaluator._clean_quote, so what the model reads and the user sees is
the posting's text.
"""
import hashlib
import re
from typing import List


def _is_consonant(word: str, i: int) -> bool:
    c = word[i]
    if c in "aeiou":
        return False
    if c == "y":
        if i == 0:
            return True
        return not _is_consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    m = 0
    i = 0
    n = len(stem)
    while i < n and _is_consonant(stem, i):
        i += 1
    while i < n:
        while i < n and not _is_consonant(stem, i):
            i += 1
        if i >= n:
            return m
        m += 1
        while i < n and _is_consonant(stem, i):
            i += 1
    return m


def _contains_vowel(s: str) -> bool:
    return any(not _is_consonant(s, i) for i in range(len(s)))


def _ends_double_consonant(word: str) -> bool:
    if len(word) < 2:
        return False
    if word[-1] != word[-2]:
        return False
    return _is_consonant(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:
    l = len(word)
    if l < 3:
        return False
    if not _is_consonant(word, l - 3) or _is_consonant(word, l - 2) or not _is_consonant(word, l - 1):
        return False
    c = word[-1]
    return c not in ("w", "x", "y")


def _step1a(w: str) -> str:
    if w.endswith("sses"):
        return w[:-2]
    if w.endswith("ies"):
        return w[:-2]
    if w.endswith("ss"):
        return w
    if w.endswith("s"):
        return w[:-1]
    return w


def _step1b(w: str) -> str:
    if w.endswith("eed"):
        return w[:-1] if _measure(w[:-3]) > 0 else w
    result = w
    removed = False
    if w.endswith("ed"):
        s = w[:-2]
        if _contains_vowel(s):
            result = s
            removed = True
    elif w.endswith("ing"):
        s = w[:-3]
        if _contains_vowel(s):
            result = s
            removed = True
    if removed:
        if result.endswith("at") or result.endswith("bl") or result.endswith("iz"):
            result += "e"
        elif _ends_double_consonant(result) and result[-1] not in "lsz":
            result = result[:-1]
        elif _measure(result) == 1 and _ends_cvc(result):
            result += "e"
    return result


def _step1c(w: str) -> str:
    if w.endswith("y") and _contains_vowel(w[:-1]):
        return w[:-1] + "i"
    return w


def _try_step(w: str, suffixes, min_m: int) -> str:
    for frm, to in suffixes:
        if w.endswith(frm):
            s = w[: len(w) - len(frm)]
            if _measure(s) > min_m:
                return s + to
            return w
    return w


_STEP2 = [
    ("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
    ("izer", "ize"), ("abli", "able"), ("alli", "al"), ("entli", "ent"),
    ("eli", "e"), ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"),
    ("ator", "ate"), ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
    ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"),
]

_STEP3 = [
    ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
    ("ical", "ic"), ("ful", ""), ("ness", ""),
]

_STEP4_SUFFIXES = [
    "ement", "ment", "ance", "ence", "able", "ible", "ant", "ent",
    "ion", "ism", "ate", "iti", "ous", "ive", "ize",
    "al", "er", "ic", "ou",
]


def _step4(w: str) -> str:
    for sfx in _STEP4_SUFFIXES:
        if w.endswith(sfx):
            s = w[: len(w) - len(sfx)]
            if sfx == "ion":
                if not s or s[-1] not in ("s", "t"):
                    continue
            return s if _measure(s) > 1 else w
    return w


def _step5a(w: str) -> str:
    if not w.endswith("e"):
        return w
    s = w[:-1]
    m = _measure(s)
    if m > 1:
        return s
    if m == 1 and not _ends_cvc(s):
        return s
    return w


def _step5b(w: str) -> str:
    if _measure(w) > 1 and _ends_double_consonant(w) and w.endswith("l"):
        return w[:-1]
    return w


def stem(word: str) -> str:
    if len(word) <= 2:
        return word.lower()
    w = word.lower()
    w = _step1a(w)
    w = _step1b(w)
    w = _step1c(w)
    w = _try_step(w, _STEP2, 0)
    w = _try_step(w, _STEP3, 0)
    w = _step4(w)
    w = _step5a(w)
    w = _step5b(w)
    return w


_TOKEN_RE = re.compile(r"[^\W_]+")
_BULLET_RE = re.compile(r"^[•·*‣◦\-]\s*")
_NUMBER_RE = re.compile(r"^\d+[.)]\s+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_MD_ESCAPE_RE = re.compile(r"\\([-_*.\[\]()#+!`&|~<>])")
_MD_LEAD_RE = re.compile(r"^[\s*_>#•·‣◦\-]+")
_MD_TRAIL_RE = re.compile(r"[\s*_#]+$")
# A line that opens with one of these continues the line above it
# ("**Nasdaq AI Platform**\n, an enterprise AI platform that ...").
_CONTINUES_RE = re.compile(r"^[,;:)]")


def clean_sentence(text: str) -> str:
    """Strip markdown noise from JD text: backslash escapes, bold markers,
    and leading/trailing bullets, headings and emphasis."""
    t = _MD_ESCAPE_RE.sub(r"\1", text or "")
    t = t.replace("**", "").replace("__", "")
    t = _MD_LEAD_RE.sub("", t)
    t = _MD_TRAIL_RE.sub("", t)
    return t.strip()


def split_sentences(text: str) -> List[str]:
    if not text:
        return []
    lines: List[str] = []
    for raw in re.split(r"\n+", text):
        line = clean_sentence(raw) if not _CONTINUES_RE.match(raw.strip()) else raw.strip()
        if not line:
            continue
        if lines and _CONTINUES_RE.match(line):
            lines[-1] = clean_sentence(f"{lines[-1]}{line}")
        else:
            lines.append(line)
    out: List[str] = []
    for line in lines:
        for s in _SENT_SPLIT_RE.split(line):
            s = _BULLET_RE.sub("", s.strip())
            s = _NUMBER_RE.sub("", s)
            s = clean_sentence(s)
            if len(s) >= 5:
                out.append(s)
    return out


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text)


def _is_run(needle: list, haystack: list) -> bool:
    n = len(needle)
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def keyword_matcher(keyword: str):
    """-> a function (sentence, tokens, stems) -> bool for one keyword, or
    None for a keyword with nothing to match on."""
    kw = keyword.strip()
    tokens = tokenize(kw)
    if not tokens:
        return None
    if re.search(r"[^\w\s\-'’./]", kw):  # "100%", "C++": the symbol is part of the keyword
        literal = re.compile(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", re.I)
        return lambda sentence, toks, stems: bool(literal.search(sentence))
    if all(t.isupper() for t in tokens if t.isalpha()) and any(len(t) > 1 and t.isalpha() for t in tokens):
        return lambda sentence, toks, stems: _is_run(tokens, toks)  # an acronym, as written
    kw_stems = [stem(t) for t in tokens]
    return lambda sentence, toks, stems: _is_run(kw_stems, stems)


def find_matched_with_context(sentences: List[str], keywords: List[str], window: int = 1) -> List[dict]:
    """Sentences that contain any keyword (see the module docstring for what
    "contain" means), plus +/-window context sentences.

    Returns [{'index': int, 's_id': 'S<index>', 'sentence': str, 'matched': bool,
    'matched_keywords': [str, ...]}, ...] ordered by index — matched_keywords is
    the original-cased keyword(s) from `keywords` that hit this sentence (empty
    for context-only sentences). Empty list overall if no keyword hits (caller
    should treat that as an auto-fail with no LLM call).
    """
    if not sentences or not keywords:
        return []

    matchers = [(k, m) for k in keywords if k.strip() for m in [keyword_matcher(k)] if m]

    hits_by_index: dict = {}  # index -> [original-cased keyword, ...]
    for i, s in enumerate(sentences):
        toks = tokenize(s)
        stems = [stem(t) for t in toks]
        hits = [orig for orig, match in matchers if match(s, toks, stems)]
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
