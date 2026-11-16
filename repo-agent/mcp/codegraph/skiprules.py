"""Skip rules: content the developer says must never be indexed (standard library only).

Two kinds, per repository (settings `skip_header_words` and `skip_patterns`):

- Header rule: the comment block at the top of a file is skipped if it contains any of the
  given words (whole word, any case) — e.g. Author, Created, Date, Copyright.
- Patterns: regular expressions (multi-line mode); every match is skipped anywhere.

Skipped text is replaced by blank lines — the same number of lines — so line numbers in
results stay correct, and the text never reaches parsing, embedding or storage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_PATTERNS, MAX_PATTERN_LEN, MAX_WORDS = 20, 300, 30
_NESTED_REPEAT = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")   # (a+)+, (x*)*, (a{2})+
_KEEP_FIRST = re.compile(r"#!|#.*coding[:=]|<\?xml")                                # shebang, encoding line
# '#' starts a comment unless it is a C preprocessor directive
_PREPROCESSOR = re.compile(r"#\s*(include|define|undef|if|ifdef|ifndef|elif|else|endif|pragma|error|warning|line|import)\b")
_LINE_COMMENTS = ("//", "--", "%", ";", "#")


def validate_pattern(p: str) -> str | None:
    """None if the regular expression may be used, else why not."""
    if not p or not p.strip():
        return "empty pattern"
    if len(p) > MAX_PATTERN_LEN:
        return f"longer than {MAX_PATTERN_LEN} characters"
    try:
        rx = re.compile(p, re.MULTILINE)
    except re.error as e:
        return f"not a valid regular expression ({e})"
    if rx.fullmatch(""):
        return "matches empty text"
    if _NESTED_REPEAT.search(p):
        return "nested repetition such as (a+)+ can take extremely long — simplify it"
    return None


def validate_word(w: str) -> str | None:
    return None if re.fullmatch(r"[\w.@-]{1,40}", w or "") else "use a single word (letters, digits, _ . @ -)"


def _blank(text: str) -> str:
    """Same number of lines, no content."""
    return "\n" * text.count("\n")


def _header_span(text: str) -> tuple[int, int] | None:
    """(start, end) offsets of the comment block at the top of the file, if any."""
    pos = 0
    lines = text.splitlines(keepends=True)
    i = 0
    if lines and _KEEP_FIRST.match(lines[0].lstrip()):          # keep a shebang / encoding line
        pos += len(lines[0])
        i = 1
    while i < len(lines) and not lines[i].strip():              # leading blank lines
        pos += len(lines[i])
        i += 1
    rest = text[pos:]
    stripped = rest.lstrip(" \t")
    lead = len(rest) - len(stripped)
    for opener, closer in (("/*", "*/"), ('"""', '"""'), ("'''", "'''"), ("<!--", "-->"), ("(*", "*)")):
        if stripped.startswith(opener):
            end = stripped.find(closer, len(opener))
            return (pos, pos + lead + end + len(closer)) if end >= 0 else None
    start, end = pos, pos
    found = False
    while i < len(lines):
        s = lines[i].lstrip()
        if s.startswith(_LINE_COMMENTS) and not _PREPROCESSOR.match(s):
            end += len(lines[i])
            found = True
            i += 1
        else:
            break
    return (start, end) if found else None


@dataclass
class SkipRules:
    header_words: list = field(default_factory=list)
    patterns: list = field(default_factory=list)

    def __post_init__(self):
        words = [w for w in self.header_words if w and w.strip()]
        self._words = re.compile(r"\b(" + "|".join(re.escape(w.strip()) for w in words) + r")\b", re.I) if words else None
        self._rx = [re.compile(p, re.MULTILINE) for p in self.patterns if p and validate_pattern(p) is None]

    def __bool__(self) -> bool:
        return bool(self._words or self._rx)

    def apply(self, text: str) -> tuple[str, list]:
        """→ (text with skipped parts blanked, [(rule, first line, last line)])."""
        hits = []
        if self._words:
            span = _header_span(text)
            if span and self._words.search(text[span[0]:span[1]]):
                a, b = span
                hits.append(("header", text.count("\n", 0, a) + 1, text.count("\n", 0, max(a, b - 1)) + 1))
                text = text[:a] + _blank(text[a:b]) + text[b:]
        for i, rx in enumerate(self._rx):
            def blank(m, i=i):
                hits.append((f"pattern {i + 1}", text.count("\n", 0, m.start()) + 1,
                             text.count("\n", 0, max(m.start(), m.end() - 1)) + 1))
                return _blank(m.group(0))
            text = rx.sub(blank, text)
        return text, hits
