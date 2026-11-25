"""Conversion logic for the structured editors (no Streamlit, unit-tested).

The PDF/LaTeX side and saved drafts keep the original data model:
- units: list of (heading, body), heading like "UNIT III: Fluid Dynamics"
- books: list of strings like "Thomas H. Cormen, Introduction to Algorithms, PHI Pub."
- credits/hours/marks: strings like "03", "03 Hours", "40 Marks"
These helpers convert between that model and what the structured inputs show.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
HEADING_STYLES = ("UNIT", "Phase", "Custom")
_HEADING_RE = re.compile(r"^(UNIT|Phase)\s+([IVXLC]+)\s*:?\s*(.*)$", re.IGNORECASE)

_ROMAN_PAIRS = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def to_roman(number: int) -> str:
    out = []
    for value, letters in ((40, "XL"), *_ROMAN_PAIRS):
        while number >= value:
            out.append(letters)
            number -= value
    return "".join(out)


def split_heading(heading: str) -> tuple[str, str]:
    """'UNIT III: Fluid Dynamics' -> ('UNIT', 'Fluid Dynamics'); other -> ('Custom', heading)."""
    match = _HEADING_RE.match(heading.strip())
    if not match:
        return "Custom", heading.strip()
    style = "UNIT" if match.group(1).upper() == "UNIT" else "Phase"
    return style, match.group(3).strip()


def detect_heading_style(units: list[tuple[str, str]]) -> str:
    """UNIT or Phase if every heading uses it, else Custom (UNIT when empty)."""
    styles = {split_heading(heading)[0] for heading, _ in units if heading.strip()}
    if not styles:
        return "UNIT"
    return styles.pop() if len(styles) == 1 else "Custom"


def compose_heading(style: str, index: int, title: str) -> str:
    """index starts at 1. 'Custom' uses the title as the whole heading."""
    title = title.strip()
    if style == "Custom":
        return title or f"Part {index}"
    return f"{style} {to_roman(index)}:" + (f" {title}" if title else "")


def panel_title(style: str, heading: str) -> str:
    """Text shown in a panel's title box for a stored heading."""
    if style == "Custom":
        return heading.strip()
    found_style, title = split_heading(heading)
    return title if found_style == style else heading.strip()


# ---------------------------------------------------------------------------
# Books: "Authors, Title, Publisher Pub."
# ---------------------------------------------------------------------------
MAX_BOOKS = 3
_PUB_SUFFIX_RE = re.compile(r"\s*\bPub\.?\s*$", re.IGNORECASE)


def _strip_pub(text: str) -> str:
    return _PUB_SUFFIX_RE.sub("", text).strip()


def _book_parts(line: str) -> list[str]:
    parts = [part.strip() for part in line.split(",") if part.strip()]
    # "..., Standard Book House, Pub." -> "Pub." belongs to the publisher.
    if len(parts) >= 2 and re.fullmatch(r"Pub\.?", parts[-1], re.IGNORECASE):
        parts[-2:] = [f"{parts[-2]} Pub."]
    return parts


def book_needs_check(line: str) -> bool:
    """True when the split into columns is a guess (not exactly 3 parts).

    Extra commas (several authors, or a comma inside the title) make the
    split ambiguous, so the user should check those rows once.
    """
    return bool(line.strip()) and len(_book_parts(line)) != 3


def parse_book(line: str) -> tuple[str, str, str]:
    """Split a book line into (authors, title, publisher).

    Commas separate authors as well as the three parts, so this is a best
    guess: the publisher is the last part, the title is the longest part
    before it, the parts before the title are authors, and anything between
    title and publisher (e.g. an edition) stays with the publisher.
    """
    parts = _book_parts(line)
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", _strip_pub(parts[0]), ""
    if len(parts) == 2:
        if _PUB_SUFFIX_RE.search(parts[1]):
            return "", parts[0], _strip_pub(parts[1])
        return parts[0], parts[1], ""
    if len(parts) == 3:  # the normal case: order is certain
        return parts[0], parts[1], _strip_pub(parts[2])
    before_publisher = parts[:-1]
    title_index = max(range(len(before_publisher)), key=lambda i: len(before_publisher[i]))
    authors = ", ".join(before_publisher[:title_index])
    title = before_publisher[title_index]
    publisher = ", ".join(before_publisher[title_index + 1:] + [parts[-1]])
    return authors, title, _strip_pub(publisher)


def format_book(authors: str, title: str, publisher: str) -> str:
    """Join the parts in the required sample format; adds 'Pub.' after the publisher."""
    authors, title, publisher = (" ".join(str(p or "").split()) for p in (authors, title, publisher))
    publisher = publisher.rstrip(",").strip()
    if publisher and not re.search(r"\bpub", publisher, re.IGNORECASE) and not publisher.lower().endswith("press"):
        publisher = f"{publisher} Pub."
    return ", ".join(part for part in (authors, title, publisher) if part)


def book_parse_is_exact(line: str) -> bool:
    """True if splitting the line and joining it again gives the same text."""
    return format_book(*parse_book(line)) == " ".join(line.split())


# ---------------------------------------------------------------------------
# Credits, hours, marks
# ---------------------------------------------------------------------------
# kind -> (format, step)
NUMBER_FIELDS = {
    "total_credits": "credits",
    "teaching_hours": "count",
    "tutorial_hours": "count",
    "practical_hours": "count",
    "exam_duration": "hours",
    "internal_evaluation": "marks",
    "end_semester_evaluation": "marks",
    "lab_total_credits": "credits",
    "lab_teaching_hours": "count",
    "lab_tutorial_hours": "count",
    "lab_practical_hours": "count",
    "lab_exam_duration": "hours",
    "lab_internal_evaluation": "marks",
    "lab_end_semester_evaluation": "marks",
}
NUMBER_STEP = {"credits": 0.5, "count": 1.0, "hours": 0.5, "marks": 1.0}
NUMBER_MAX = {"credits": 30.0, "count": 40.0, "hours": 12.0, "marks": 1000.0}
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_number(text: str) -> float | None:
    match = _NUMBER_RE.search(str(text or ""))
    return float(match.group()) if match else None


def _two_digits(value: float) -> str:
    return f"{int(value):02d}" if float(value).is_integer() else f"{value:g}"


def format_number(kind: str, value: float) -> str:
    """Write a number the way the syllabus PDF shows it: 03 / 03 Hours / 40 Marks."""
    if kind == "hours":
        return f"{_two_digits(value)} Hours"
    if kind == "marks":
        return f"{int(value) if float(value).is_integer() else f'{value:g}'} Marks"
    return _two_digits(value)
