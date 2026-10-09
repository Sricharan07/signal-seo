"""Versioned deterministic editorial heuristics, not a truth or authority gate."""

import re
import statistics
import unicodedata
from collections import Counter

from signal_core.writing_style import BANNED_PHRASES, language_of

QUALITY_VERSION = "writing-quality-0136-v1"
THRESHOLDS = {
    "banned_hits": 0,
    "filler_density": 0.03,
    "hedge_density": 0.04,
    "repeated_ngram_ratio": 0.15,
    "sentence_length_variance": 4.0,
    "passive_ratio": 0.25,
    "readability": 35.0,
    "paragraph_words": 80,
}
FILLER = (
    "it's important to note",
    "in order to",
    "as a matter of fact",
    "in conclusion",
    "at the end of the day",
    "needless to say",
    "it goes without saying",
)
HEDGES = ("perhaps", "possibly", "arguably", "somewhat", "generally", "might", "may")


def tokens(text):
    # Combining marks belong to Telugu words, not separate word fragments.
    words, current = [], []
    for char in text.casefold():
        if unicodedata.category(char)[0] in {"L", "N", "M"} or (
            char in {"'", "\u2019"} and current
        ):
            current.append(char)
        elif current:
            words.append("".join(current).strip("'\u2019"))
            current = []
    if current:
        words.append("".join(current).strip("'\u2019"))
    return words


def quality_report(paragraphs, *, language=None):
    text = "\n".join(paragraphs)
    normalized = text.casefold().replace("\u2019", "'")
    words = tokens(text)
    actual_language = language_of(text)
    language = language if language and language != "und" else actual_language
    sentences = [s.strip() for s in re.split(r"[.!?\u0964]+(?:\s+|$)|\n", text) if s.strip()]
    lengths = [len(tokens(s)) for s in sentences]
    english = language == "en"

    def hits(phrases):
        return sum(
            len(re.findall(r"(?<!\w)" + re.escape(p) + r"(?!\w)", normalized)) for p in phrases
        )

    banned = [p for p in BANNED_PHRASES if hits((p,))]
    patterns = (
        r"\bwhether (?:you are|you're)\b.+?\bor\b",
        r"(?:^|[.!?]\s+)(?:ready to|want to|looking to|why settle|what if)\b[^?]*\?",
    )
    pattern_hits = sum(len(re.findall(p, normalized)) for p in patterns)
    ngrams = Counter(tuple(words[i : i + 5]) for i in range(max(0, len(words) - 4)))
    repeated = sum(n - 1 for n in ngrams.values()) / max(1, sum(ngrams.values()))
    passive = sum(
        bool(re.search(r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?\w+ed\b", s, re.I))
        for s in sentences
    ) / max(1, len(sentences))
    syllables = sum(max(1, len(re.findall(r"[aeiouy]+", w))) for w in words)
    readability = (
        206.835
        - 1.015 * len(words) / max(1, len(sentences))
        - 84.6 * syllables / max(1, len(words))
    )
    metrics = {
        "banned_hits": hits(BANNED_PHRASES) + pattern_hits,
        "filler_density": hits(FILLER) / max(1, len(words)),
        "hedge_density": hits(HEDGES) / max(1, len(words)),
        "repeated_ngram_ratio": repeated,
        "sentence_length_variance": statistics.pvariance(lengths) if lengths else 0.0,
        "passive_ratio": passive if english else None,
        "readability": readability if english else None,
        "paragraph_words": max((len(tokens(p)) for p in paragraphs), default=0),
    }
    reasons = []
    if language in {"te", "te-en"} and actual_language != language:
        reasons.append("target_language_mismatch")
    elif language == "en" and actual_language != "en":
        reasons.append("target_language_mismatch")
    for name in ("banned_hits", "filler_density", "hedge_density", "paragraph_words"):
        if metrics[name] > THRESHOLDS[name]:
            reasons.append(name)
    # Short metadata/headings do not have meaningful variance, repetition or readability.
    if len(words) >= 60:
        for name in ("repeated_ngram_ratio", "passive_ratio"):
            if metrics[name] is not None and metrics[name] > THRESHOLDS[name]:
                reasons.append(name)
        for name in ("sentence_length_variance", "readability"):
            if metrics[name] is not None and metrics[name] < THRESHOLDS[name]:
                reasons.append(name)
    return {
        "version": QUALITY_VERSION,
        "state": "low_quality" if reasons else "passed",
        "language": language,
        "actual_language": actual_language,
        "metrics": metrics,
        "reasons": reasons,
        "banned_phrases": banned,
        "thresholds": THRESHOLDS.copy(),
        "language_limitations": [] if english else ["english_passive_and_readability_not_assessed"],
    }
