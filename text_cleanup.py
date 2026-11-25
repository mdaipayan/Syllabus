"""Rule-based clean-up of syllabus text: spaces, lines, bullets, unit headings.

Grammar is handled separately (LanguageTool, in app.py). The rules here are
deterministic and work offline, so they are safe to run on every import.
Kept free of Streamlit so it can be unit-tested.

The text format is the one used by the editor boxes:
- list fields: one item per line
- units: blocks separated by a blank line; first line of a block is the heading
- other fields: a single line
"""

from __future__ import annotations

import re

LIST_FIELDS = {
    "objectives",
    "outcomes",
    "textbooks",
    "reference_books",
    "lab_objectives",
    "lab_outcomes",
    "experiments",
}
BOOK_FIELDS = {"textbooks", "reference_books"}
UNIT_FIELD = "units"

# Characters that PDF extraction and copy-paste often bring in.
_CHAR_FIXES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ", "\t": " ",
    "​": "", "‌": "", "‍": "", "﻿": "", "­": "",
}

# Numbering/bullets typed in front of list items. The PDF adds its own
# numbers (1, 2, ... / CO1, CO2, ...), so these would print twice.
_LEADING_MARKER_RE = re.compile(
    r"^(?:"
    r"[•●▪◦■□➢►✓\-–—*]\s*"                       # bullets
    r"|(?:CO|LO|CLO|LCO)\s*[-.]?\s*\d+\s*[:.)\-–]?\s+"  # CO1:  CO-2  LO3)
    r"|\(?\d{1,2}\s*[.)](?!\d)\s*"                 # 1.  2)  (3)  -- not 3.5
    r"|\(?[a-hA-H][.)]\s+"                          # a)  b.
    r"|\(?(?:i{1,3}|iv|vi{0,3}|ix|x)[.)]\s+"        # i)  iv.
    r")"
)

_UNIT_HEADING_RE = re.compile(
    r"^unit\s*[-–:.]?\s*([ivxl]+|\d{1,2})(?![a-z0-9])\s*[:\-–.]*\s*(.*)$", re.IGNORECASE
)
_ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
          "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]


def tidy_line(line: str) -> str:
    """Fix spacing and punctuation spacing inside one line."""
    for bad, good in _CHAR_FIXES.items():
        line = line.replace(bad, good)
    line = re.sub(r" {2,}", " ", line).strip()
    line = re.sub(r"\s+([,.;:!?)\]])", r"\1", line)          # "word ," -> "word,"
    line = re.sub(r"([(\[])\s+", r"\1", line)                 # "( x" -> "(x"
    line = re.sub(r"([,;:])\1+", r"\1", line)                 # ",," -> ","
    line = re.sub(r"([,;:])(?=[A-Za-z(])", r"\1 ", line)      # "a,b" -> "a, b"
    line = re.sub(r"(?<=[a-z]{2})\.(?=[A-Z][a-z])", ". ", line)  # "end.Next" -> "end. Next"
    return line


def _join_wrapped(lines: list[str]) -> list[str]:
    """Join lines that a PDF broke in the middle of a sentence.

    A line is treated as a continuation when it starts with a small letter
    and the line before it does not end a sentence or a heading.
    """
    out: list[str] = []
    for line in lines:
        if out and line[:1].islower() and not out[-1].endswith((".", ":", "?", "!")):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return out


def _clean_item(line: str, is_book: bool) -> str:
    line = _LEADING_MARKER_RE.sub("", line, count=1).strip()
    line = line.rstrip(",;").strip()
    if not is_book and line[:1].islower():
        line = line[0].upper() + line[1:]
    return line


def tidy_list(text: str, *, is_book: bool = False, join_wrapped: bool = False) -> str:
    lines = [tidy_line(line) for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    if join_wrapped:
        lines = _join_wrapped(lines)
    items = [_clean_item(line, is_book) for line in lines]
    return "\n".join(item for item in items if item)


def normalize_unit_heading(line: str) -> str:
    """'Unit 3 - fluid dynamics' -> 'UNIT III: Fluid dynamics'. Other lines unchanged."""
    match = _UNIT_HEADING_RE.match(line)
    if not match:
        return line
    number, title = match.group(1), match.group(2).strip()
    if number.isdigit():
        if not 1 <= int(number) <= 20:
            return line
        number = _ROMAN[int(number)]
    title = title[:1].upper() + title[1:]
    return f"UNIT {number.upper()}:" + (f" {title}" if title else "")


# "i. ..." / "a) ..." sub-items inside a unit body: the PDF renders these as
# nested lists, so their marker letter must stay small.
_BODY_MARKER_RE = re.compile(r"^(?:[ivxlc]+|[a-z])[.)]\s")


def _capitalize_body_line(line: str) -> str:
    if line[:1].islower() and not _BODY_MARKER_RE.match(line):
        return line[0].upper() + line[1:]
    return line


def tidy_title(text: str) -> str:
    """One-line title: spacing fixes and a capital first letter."""
    line = tidy_line(text)
    return line[:1].upper() + line[1:]


def tidy_unit_body(text: str, *, join_wrapped: bool = False) -> str:
    """Body of one unit: spacing fixes, no blank lines, capital line starts."""
    lines = [tidy_line(line) for line in text.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    if join_wrapped:
        lines = _join_wrapped(lines)
    return "\n".join(_capitalize_body_line(line) for line in lines)


def tidy_units(text: str, *, join_wrapped: bool = False) -> str:
    """Tidy each unit; start a new unit at every 'UNIT ...' line, even
    if the blank line before it was forgotten."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = tidy_line(raw)
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        if current and _UNIT_HEADING_RE.match(line):
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)

    out: list[str] = []
    for block in blocks:
        heading, body = normalize_unit_heading(block[0]), block[1:]
        if join_wrapped:
            body = _join_wrapped(body)
        body = [_capitalize_body_line(line) for line in body]
        out.append("\n".join([heading, *body]))
    return "\n\n".join(out)


def clean_field_text(field_name: str, text: str, *, join_wrapped: bool = False) -> str:
    """Clean the editor-box text of one field (not grammar)."""
    if field_name == UNIT_FIELD:
        return tidy_units(text, join_wrapped=join_wrapped)
    if field_name in LIST_FIELDS:
        return tidy_list(text, is_book=field_name in BOOK_FIELDS, join_wrapped=join_wrapped)
    return tidy_line(text)
