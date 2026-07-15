"""Extract syllabus fields from an uploaded PDF and map them onto SyllabusData.

The parser is intentionally tolerant: PDFs are messy, so every field is
optional. Whatever can be recognised is returned in a dict keyed by
``SyllabusData`` field names; everything else is simply left untouched so the
existing editor values (or defaults) remain in place.
"""

from __future__ import annotations

import io
import re
from typing import Dict, List, Tuple

try:
    import pdfplumber
except ImportError:  # pragma: no cover - surfaced to the user in the UI
    pdfplumber = None


# Lines lose their inter-word spaces in some justified paragraphs unless the
# horizontal tolerance is tightened a little below pdfplumber's default of 3.
_X_TOLERANCE = 1.5


class PdfExtractionError(RuntimeError):
    """Raised when the PDF cannot be opened or no usable text is found."""


def _extract_text(pdf_bytes: bytes) -> str:
    if pdfplumber is None:
        raise PdfExtractionError(
            "pdfplumber is not installed. Add 'pdfplumber' to requirements.txt."
        )
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = [page.extract_text(x_tolerance=_X_TOLERANCE) or "" for page in pdf.pages]
    except Exception as exc:  # pragma: no cover - depends on the uploaded file
        raise PdfExtractionError(f"Could not read the PDF: {exc}") from exc

    text = "\n".join(pages)
    text = _strip_signature_footer(text)
    if not text.strip():
        raise PdfExtractionError(
            "No selectable text was found. Scanned/image-only PDFs are not supported."
        )
    return text


# The per-page signature footer is extracted as a text line on every page and
# would otherwise bleed into whichever section spans the page break (last
# outcome, a unit body, or the final reference book).
_SIGNATURE_LINE = re.compile(
    r"^\s*(?:BOS\s+Chairperson|Dean\s*\(Academic\)|Principal)"
    r"(?:\s+(?:BOS\s+Chairperson|Dean\s*\(Academic\)|Principal))*\s*$",
    re.IGNORECASE,
)


def _strip_signature_footer(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not _SIGNATURE_LINE.match(line)
    )


def _dehyphenate(lines: List[str]) -> List[str]:
    """Join words split across a line break, e.g. 'Tec-' + 'tonics' -> 'Tectonics'."""
    merged: List[str] = []
    for line in lines:
        if merged and re.search(r"[A-Za-z]-$", merged[-1]) and re.match(r"[a-z]", line):
            merged[-1] = merged[-1][:-1] + line
        else:
            merged.append(line)
    return merged


def _clean_lines(block: str) -> List[str]:
    return [line.strip() for line in block.splitlines() if line.strip()]


def _section(text: str, start: str, ends: List[str]) -> str:
    """Return the slice of ``text`` between ``start`` and the first of ``ends``."""
    start_match = re.search(start, text, re.IGNORECASE)
    if not start_match:
        return ""
    body = text[start_match.end():]
    cut = len(body)
    for end in ends:
        end_match = re.search(end, body, re.IGNORECASE)
        if end_match:
            cut = min(cut, end_match.start())
    return body[:cut]


def _first(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _join_numbered_items(block: str, item_start: str) -> List[str]:
    """Group wrapped lines into items, each beginning with ``item_start``."""
    items: List[str] = []
    current: List[str] = []
    for raw in _dehyphenate(_clean_lines(block)):
        marker = re.match(item_start, raw)
        if marker:
            if current:
                items.append(" ".join(current).strip())
            current = [raw[marker.end():].strip()]
        elif current:
            current.append(raw)
    if current:
        items.append(" ".join(current).strip())
    return [item for item in items if item]


def _parse_title(text: str) -> str:
    head = text[: re.search(r"Total\s+Credits", text, re.IGNORECASE).start()] \
        if re.search(r"Total\s+Credits", text, re.IGNORECASE) else ""
    lines = _clean_lines(head)
    if not lines:
        return ""
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _parse_units(text: str) -> List[Tuple[str, str]]:
    block = _section(
        text,
        r"COURSE\s+CONTENT",
        [r"Text\s*Books?\s*:", r"Reference\s*Books?\s*:"],
    )
    if not block:
        return []

    lines = _dehyphenate(_clean_lines(block))
    heading_re = re.compile(r"^Unit\s+([IVXLC\d]+)\s*[:.\-]?\s*(.*)$", re.IGNORECASE)

    units: List[Tuple[str, str]] = []
    heading: str | None = None
    body: List[str] = []
    for line in lines:
        match = heading_re.match(line)
        if match:
            if heading is not None:
                units.append((heading, " ".join(body).strip()))
            numeral = match.group(1).upper()
            name = match.group(2).strip()
            heading = f"UNIT {numeral}: {name}" if name else f"UNIT {numeral}"
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        units.append((heading, " ".join(body).strip()))
    return units


def _join_phase_body(lines: List[str]) -> str:
    """Group a phase's wrapped lines into one paragraph per a./i./numbered item."""
    marker = re.compile(r"^(?:[A-Za-z]|[IVXLC]+|\d+)[.)]\s|^\(Maps", re.IGNORECASE)
    items: List[str] = []
    current: List[str] = []
    for line in lines:
        if marker.match(line) and current:
            items.append(" ".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        items.append(" ".join(current).strip())
    return "\n".join(item for item in items if item)


def _parse_weekly_plan(text: str) -> List[Tuple[str, str]]:
    """Parse the Project/Internship 'Weekly Work Plan' phases into units."""
    block = _section(text, r"Weekly\s+Work\s+Plan", [r"Text\s*Books?\s*:"])
    if not block:
        return []

    lines = _dehyphenate(_clean_lines(block))
    # Phases are enumerated as "I. Phase I: ..." or "1. Phase I: ...".
    phase_re = re.compile(r"^(?:[IVXLC]+|\d+)[.)]\s+(Phase\b.*)$", re.IGNORECASE)

    units: List[Tuple[str, str]] = []
    heading: str | None = None
    body: List[str] = []
    for line in lines:
        match = phase_re.match(line)
        if match:
            if heading is not None:
                units.append((heading, _join_phase_body(body)))
            heading = match.group(1).strip()
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        units.append((heading, _join_phase_body(body)))
    return units


# Theory-page field names and their Lab-page equivalents. When a PDF is
# recognised as a lab syllabus, whatever was parsed is re-keyed through this
# map so the values land on the Lab editor page instead of the Theory page.
_LAB_FIELD_MAP = {
    "title": "lab_table_title",
    "total_credits": "lab_total_credits",
    "subject_code": "lab_subject_code",
    "teaching_hours": "lab_teaching_hours",
    "tutorial_hours": "lab_tutorial_hours",
    "practical_hours": "lab_practical_hours",
    "exam_duration": "lab_exam_duration",
    "internal_evaluation": "lab_internal_evaluation",
    "end_semester_evaluation": "lab_end_semester_evaluation",
    "objectives": "lab_objectives",
    "outcomes": "lab_outcomes",
}


def _looks_like_lab(text: str, title: str, has_units: bool) -> bool:
    """Heuristic: a unit-less syllabus titled '... Lab' (or carrying an
    experiments / practical-assignments list) is a lab page."""
    if has_units:
        return False
    if re.search(r"\blab(?:oratory)?\b", title, re.IGNORECASE):
        return True
    return bool(
        re.search(r"\bexperiments?\b|practical\s+assignments", text, re.IGNORECASE)
    )


def _parse_lab_experiments(text: str, title: str) -> Tuple[str, List[str]]:
    """Return (experiments_heading, experiments) from the lab content page."""
    block = ""
    if title:
        # The content page repeats the title as an all-caps header; match it
        # case-sensitively so the mixed-case title on page 1 is skipped.
        header = r"\s+".join(re.escape(word.upper()) for word in title.split())
        block = _section(text, f"(?-i:{header})", [r"\*\s*Above\s+is"])
    if not block:
        block = _section(text, r"Course\s+Outcomes", [r"\*\s*Above\s+is"])

    lines = _dehyphenate(_clean_lines(block))
    item_re = re.compile(r"^\d+[.)]\s+")
    heading = ""
    for line in lines:
        if item_re.match(line):
            break
        heading = line
    if title and re.sub(r"\s+", " ", heading).upper() == re.sub(r"\s+", " ", title).upper():
        heading = ""  # just the repeated page header, not a real heading

    experiments = _join_numbered_items(block, r"^\d+[.)]\s+")
    return heading, experiments


def _lab_fields(theory_result: Dict[str, object], text: str, title: str) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for theory_key, lab_key in _LAB_FIELD_MAP.items():
        if theory_key in theory_result:
            result[lab_key] = theory_result[theory_key]
    if title:
        result["lab_section_title"] = title
    heading, experiments = _parse_lab_experiments(text, title)
    if experiments:
        result["experiments"] = experiments
        if heading:
            result["experiments_heading"] = heading
    return result


def extract_syllabus_fields(pdf_bytes: bytes) -> Dict[str, object]:
    """Parse ``pdf_bytes`` and return a dict of recognised SyllabusData fields."""
    return _fields_from_text(_extract_text(pdf_bytes))


def _fields_from_text(text: str) -> Dict[str, object]:
    result: Dict[str, object] = {}

    title = _parse_title(text)
    if title:
        result["title"] = title

    scalar_fields = {
        "total_credits": r"Total\s+Credits\s*:?\s*([^\n]+?)(?:\s{2,}|Subject\s+Code|$)",
        "subject_code": r"Subject\s+Code\s*:?\s*([^\n]+?)(?:\s{2,}|$)",
        "teaching_hours": r"Teaching\s+Hrs?/Week\s*:?\s*([^\n]+?)(?:\s{2,}|Duration|$)",
        "tutorial_hours": r"Tutorials?\s+Hrs?/Week\s*:?\s*([^\n]+?)(?:\s{2,}|Internal|$)",
        "practical_hours": r"Practical\s+Hrs?/Week\s*:?\s*([^\n]+?)(?:\s{2,}|End\s+Semester|$)",
        "exam_duration": r"Duration\s+of\s+End\s+Semester\s+Exam\s*:?\s*([^\n]+?)(?:\s{2,}|$)",
        "internal_evaluation": r"Internal\s+Evaluation\s*:?\s*([^\n]+?)(?:\s{2,}|$)",
        "end_semester_evaluation": r"End\s+Semester\s+Examination\s*:?\s*([^\n]+?)(?:\s{2,}|$)",
    }
    for field_name, pattern in scalar_fields.items():
        value = _first(pattern, text)
        if value:
            result[field_name] = value

    objectives_block = _section(
        text, r"Course\s+Objectives", [r"Course\s+Outcomes"]
    )
    objectives = _join_numbered_items(objectives_block, r"^\d+[.)]?\s+")
    if objectives:
        result["objectives"] = objectives

    outcome_ends = [
        r"COURSE\s+CONTENT",
        r"Weekly\s+Work\s+Plan",
        r"Unit\s+[IVXLC\d]+",
        r"Text\s*Books?\s*:",
    ]
    if title:
        # The course title is repeated as an all-caps running header on the
        # content page (often wrapped across lines); stop before it so it does
        # not bleed into the last outcome. Match case-sensitively against the
        # upper-cased title so ordinary words in the outcome text (e.g.
        # "project", "internship") are never mistaken for the header.
        header = r"\s+".join(re.escape(word.upper()) for word in title.split())
        outcome_ends.insert(0, f"(?-i:{header})")
    outcomes_block = _section(text, r"Course\s+Outcomes", outcome_ends)
    outcomes_block = re.sub(
        r"After\s+completion[^\n]*", "", outcomes_block, flags=re.IGNORECASE
    )
    outcomes = _join_numbered_items(outcomes_block, r"^CO\s*\d+[.)]?\s+")
    if outcomes:
        result["outcomes"] = outcomes

    units = _parse_units(text)
    weekly_plan_format = False
    if not units:
        # Project/Internship syllabi replace the unit list with a phase-based
        # weekly work plan and carry no text/reference books.
        weekly = _parse_weekly_plan(text)
        if weekly:
            units = weekly
            weekly_plan_format = True
    if units:
        result["units"] = units

    if _looks_like_lab(text, title, bool(units)):
        # Lab syllabi have no units/books; re-key everything onto the Lab page.
        return _lab_fields(result, text, title)

    textbooks_block = _section(
        text, r"Text\s*Books?\s*:", [r"Reference\s*Books?\s*:"]
    )
    textbooks = _join_numbered_items(textbooks_block, r"^\d+[.)]?\s+")
    if textbooks:
        result["textbooks"] = textbooks
    elif weekly_plan_format:
        result["textbooks"] = []

    references_block = _section(
        text, r"Reference\s*Books?\s*:", [r"^\s*$\Z"]
    )
    references = _join_numbered_items(references_block, r"^\d+[.)]?\s+")
    if references:
        result["reference_books"] = references
    elif weekly_plan_format:
        result["reference_books"] = []

    return result
