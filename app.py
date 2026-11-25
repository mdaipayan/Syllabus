from __future__ import annotations

import inspect
import re
from dataclasses import fields
from typing import Any

import pandas as pd
import streamlit as st

from draft_io import (
    DraftError,
    draft_file_name,
    draft_from_json,
    draft_to_json,
    subject_file_stem,
)
from pdf_extractor import PdfExtractionError, extract_syllabus_fields
from syllabus_generator import SyllabusData, compile_pdf, render_lab_latex, render_theory_latex
from structured_fields import (
    HEADING_STYLES,
    MAX_BOOKS,
    NUMBER_FIELDS,
    NUMBER_MAX,
    NUMBER_STEP,
    book_needs_check,
    compose_heading,
    detect_heading_style,
    format_book,
    format_number,
    panel_title,
    parse_book,
    parse_number,
    split_heading,
)
from text_cleanup import clean_field_text, tidy_title, tidy_unit_body, tidy_units

try:
    import language_tool_python
    _GRAMMAR_AVAILABLE = True
except ImportError:
    _GRAMMAR_AVAILABLE = False


st.set_page_config(page_title="Syllabus Formatter", layout="wide")
st.title("Syllabus Formatter")
st.caption("Edit each syllabus page separately, generate LaTeX, and download a compiled PDF. Developed by D. Mandal")

EDITOR_KEY_PREFIX = "editor__"
TEXT_LIST_FIELDS = {
    "objectives",
    "outcomes",
    "textbooks",
    "reference_books",
    "lab_objectives",
    "lab_outcomes",
    "experiments",
}
UNIT_FIELD = "units"
PDF_STATE_KEYS = ("theory_syllabus_pdf_bytes", "lab_syllabus_pdf_bytes")

# Fields to grammar-check per page, and their human-readable labels.
_GRAMMAR_FIELDS: dict[str, list[str]] = {
    "Theory Page": ["objectives", "outcomes", "units", "textbooks", "reference_books"],
    "Lab Page": ["lab_objectives", "lab_outcomes", "experiments"],
}
_GRAMMAR_LABELS: dict[str, str] = {
    "objectives": "Course Objectives",
    "outcomes": "Course Outcomes",
    "units": "Units",
    "textbooks": "Text Books",
    "reference_books": "Reference Books",
    "lab_objectives": "Lab Objectives",
    "lab_outcomes": "Lab Outcomes",
    "experiments": "Experiments",
}


# LanguageTool rules that misfire on line-based syllabus text (each list item
# is one line without a full stop, headings are all caps, etc.).
_IGNORED_GRAMMAR_RULES = {
    "UPPERCASE_SENTENCE_START",
    "WHITESPACE_RULE",
    "CONSECUTIVE_SPACES",
    "PUNCTUATION_PARAGRAPH_END",
}
# Author names and publishers are flagged as spelling errors, so book lists are
# never changed by "Apply All"; they can still be fixed one field at a time.
_APPLY_ALL_EXCLUDED = {"textbooks", "reference_books"}


@st.cache_resource(show_spinner="Loading grammar engine… (first run may take a minute)")
def _load_grammar_tool_cached() -> "language_tool_python.LanguageTool":
    """Start the local engine (needs Java); fall back to the public LanguageTool API.

    Raises if both fail. Exceptions are not cached by st.cache_resource, so a
    later click retries instead of staying broken until the app restarts.
    """
    try:
        return language_tool_python.LanguageTool("en-US")
    except Exception:
        return language_tool_python.LanguageToolPublicAPI("en-US")


def _load_grammar_tool() -> "language_tool_python.LanguageTool | None":
    if not _GRAMMAR_AVAILABLE:
        return None
    try:
        return _load_grammar_tool_cached()
    except Exception:
        return None


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first attribute that exists; names differ across language_tool_python versions."""
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _check_text(text: str) -> tuple[list[dict], str]:
    """Return (matches_info, corrected_text). Returns empty list on failure."""
    tool = _load_grammar_tool()
    if tool is None or not text.strip():
        return [], text
    matches = [
        m for m in tool.check(text)
        if _attr(m, "ruleId", "rule_id", default="") not in _IGNORED_GRAMMAR_RULES
    ]
    if not matches:
        return [], text
    corrected = language_tool_python.utils.correct(text, matches)
    info = [
        {
            "message": _attr(m, "message", default=""),
            "context": _attr(m, "context", default=""),
            "context_offset": _attr(m, "offsetInContext", "offset_in_context", default=None),
            "length": _attr(m, "errorLength", "error_length", "length", default=0),
            "replacements": list(_attr(m, "replacements", default=[]))[:4],
            "rule": _attr(m, "ruleId", "rule_id", default=""),
        }
        for m in matches
    ]
    return info, corrected


def _context_with_marker(match: dict) -> str:
    """Return the context line plus a ^^^ line under the error."""
    ctx = str(match["context"]).replace("\n", " ")
    offset, length = match.get("context_offset"), match.get("length") or 0
    if offset is None or length <= 0:
        return ctx
    return f"{ctx}\n{' ' * offset}{'^' * length}"


def _grammar_results_key(page_name: str) -> str:
    return f"_grammar_results_{page_name}"


def _apply_grammar_fixes(page_name: str, field_names: list[str]) -> None:
    """Button callback: runs before widgets are drawn, so widget state can be set.

    A field is only overwritten if it still holds the exact text that was
    checked; otherwise the user's newer edits would be lost.
    """
    results = st.session_state.get(_grammar_results_key(page_name), {})
    skipped: list[str] = []
    for field_name in field_names:
        result = results.pop(field_name, None)
        if result is None:
            continue
        if _current_text(field_name) == result["original"]:
            _set_text(field_name, result["corrected"])
        else:
            skipped.append(_GRAMMAR_LABELS.get(field_name, field_name))
    st.session_state[f"_grammar_skipped_{page_name}"] = skipped
    clear_generated_pdfs()


AUTO_GRAMMAR_KEY = "_auto_grammar_enabled"
_AUTO_NOTICE_KEY = "_auto_grammar_notice"
# Fields auto-correct may grammar-check: every grammar field except book lists.
_AUTO_GRAMMAR_FIELDS = {
    name
    for names in _GRAMMAR_FIELDS.values()
    for name in names
    if name not in _APPLY_ALL_EXCLUDED
}
# Every field edited as text (everything except the checkboxes).
_TEXT_FIELDS = [
    f.name
    for f in fields(SyllabusData)
    if not isinstance(getattr(SyllabusData(), f.name), bool) and f.name not in NUMBER_FIELDS
]


def _field_label(field_name: str) -> str:
    return _GRAMMAR_LABELS.get(field_name, field_name.replace("_", " ").title())


def _current_text(field_name: str) -> str:
    """Latest text of a field, in the editor's text form.

    Inside a button/edit callback, editors drawn in the last run hold the
    newest text. Otherwise the master copy in `data` is up to date.
    """
    if field_name in st.session_state.get(_RENDERED_NOW_KEY, set()):
        value = _read_widget(field_name)
        if value is not _MISSING:
            return str(value)
    return str(widget_value_from_data(st.session_state.data, field_name))


def _set_text(field_name: str, text: str) -> None:
    """Write a field to both the master copy and its editor.

    Only call from a callback or before the editor is drawn in this run.
    """
    setattr(st.session_state.data, field_name, data_value_from_widget(field_name, text))
    if field_name == UNIT_FIELD or field_name in BOOK_FIELDS:
        _refresh_widget(field_name)  # panels/tables are rebuilt from the data
    else:
        st.session_state[editor_key(field_name)] = text  # keep the exact text


def _clean_one(field_name: str, text: str, *, join_wrapped: bool, grammar: bool) -> tuple[str, int]:
    """Lines/spaces/bullets/headings, then grammar. Returns (text, grammar fixes)."""
    cleaned = clean_field_text(field_name, text, join_wrapped=join_wrapped)
    if not grammar or field_name not in _AUTO_GRAMMAR_FIELDS or not cleaned.strip():
        return cleaned, 0
    matches, corrected = _check_text(cleaned)
    if not matches:
        return cleaned, 0
    # Grammar fixes can leave odd spacing; tidy once more.
    return clean_field_text(field_name, corrected), len(matches)


def clean_fields(field_names: list[str], *, join_wrapped: bool, grammar: bool, label: str) -> None:
    """Clean several fields and leave a notice with Undo.

    join_wrapped=True also joins lines a PDF broke mid-sentence; used for
    imports and "Clean up all fields", not for normal typing.
    """
    grammar_ok = grammar and _load_grammar_tool() is not None
    changes: dict[str, tuple[str, str]] = {}
    grammar_fixes = 0
    failed: list[str] = []
    for field_name in field_names:
        old = _current_text(field_name)
        try:
            new, fixes = _clean_one(field_name, old, join_wrapped=join_wrapped, grammar=grammar_ok)
        except Exception:
            failed.append(_field_label(field_name))
            new, fixes = clean_field_text(field_name, old, join_wrapped=join_wrapped), 0
        if new != old:
            _set_text(field_name, new)
            changes[field_name] = (old, new)
            grammar_fixes += fixes

    notice: dict[str, Any] = {"label": label, "changes": changes, "grammar_fixes": grammar_fixes}
    if grammar and not grammar_ok:
        notice["warning"] = (
            "Grammar was skipped: the grammar engine did not start (no Java, and the "
            "public LanguageTool service could not be reached). Lines and spaces were still fixed."
        )
    elif failed:
        notice["warning"] = "Grammar check failed for: " + ", ".join(failed) + "."
    if changes or "warning" in notice:
        st.session_state[_AUTO_NOTICE_KEY] = notice
    if changes:
        clear_generated_pdfs()


def _on_editor_change(field_name: str) -> None:
    """on_change callback for editor boxes.

    Runs before the script redraws the boxes, so it may replace the text.
    With auto-correct on, the edited box is cleaned straight away.
    """
    clear_generated_pdfs()
    if st.session_state.get(AUTO_GRAMMAR_KEY):
        clean_fields([field_name], join_wrapped=False, grammar=True, label=_field_label(field_name))


def _clean_all_fields() -> None:
    clean_fields(_TEXT_FIELDS, join_wrapped=True, grammar=True, label="Clean up all fields")


def _undo_auto_correct() -> None:
    notice = st.session_state.pop(_AUTO_NOTICE_KEY, None) or {}
    for field_name, (old, new) in notice.get("changes", {}).items():
        # Only undo boxes that still hold what the clean-up wrote.
        if _current_text(field_name) == new:
            _set_text(field_name, old)
    clear_generated_pdfs()


def render_auto_correct_notice() -> None:
    notice = st.session_state.get(_AUTO_NOTICE_KEY)
    if not notice:
        return
    if notice.get("warning"):
        st.warning(notice["warning"])
    changes = notice.get("changes", {})
    if not changes:
        st.session_state.pop(_AUTO_NOTICE_KEY, None)
        return
    labels = ", ".join(_field_label(name) for name in changes)
    grammar = notice.get("grammar_fixes", 0)
    grammar_text = f", including **{grammar}** grammar fix(es)" if grammar else ""
    col_msg, col_undo, col_ok = st.columns([6, 1, 1])
    with col_msg:
        st.success(
            f"✨ **{notice['label']}:** corrected {len(changes)} field(s){grammar_text} — {labels}."
        )
    with col_undo:
        st.button("↩️ Undo", key="_auto_grammar_undo", on_click=_undo_auto_correct,
                  width="stretch")
    with col_ok:
        st.button("OK", key="_auto_grammar_ok", width="stretch",
                  on_click=lambda: st.session_state.pop(_AUTO_NOTICE_KEY, None))


def _clear_grammar_results(page_name: str) -> None:
    st.session_state.pop(_grammar_results_key(page_name), None)
    st.session_state.pop(f"_grammar_skipped_{page_name}", None)


def default_data() -> SyllabusData:
    return SyllabusData()


def editor_key(field_name: str) -> str:
    return f"{EDITOR_KEY_PREFIX}{field_name}"


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_units(value: str) -> list[tuple[str, str]]:
    units: list[tuple[str, str]] = []
    # Tolerate separator lines that contain stray spaces/tabs, and CRLF input.
    normalized = value.replace("\r\n", "\n")
    raw_blocks = [block.strip() for block in re.split(r"\n[ \t]*\n", normalized) if block.strip()]
    for block in raw_blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        heading = lines[0]
        body = "\n".join(lines[1:]) if len(lines) > 1 else ""
        units.append((heading, body))
    return units


def block_from_units(units: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"{heading}\n{body}" for heading, body in units)


def widget_value_from_data(data: SyllabusData, field_name: str) -> Any:
    value = getattr(data, field_name)
    if field_name in NUMBER_FIELDS:
        number = parse_number(value)
        return float(number) if number is not None else 0.0
    if field_name in TEXT_LIST_FIELDS:
        return "\n".join(value)
    if field_name == UNIT_FIELD:
        return block_from_units(value)
    return value


def data_value_from_widget(field_name: str, value: Any) -> Any:
    if field_name in NUMBER_FIELDS:
        number = value if isinstance(value, (int, float)) else parse_number(str(value))
        return format_number(NUMBER_FIELDS[field_name], float(number or 0))
    if field_name in TEXT_LIST_FIELDS:
        return split_lines(str(value))
    if field_name == UNIT_FIELD:
        return split_units(str(value))
    return value


def syllabus_field_names() -> list[str]:
    return [field.name for field in fields(SyllabusData)]


# Which editor fields had a widget on screen in the current / previous run.
_RENDERED_NOW_KEY = "_editor_rendered_now"
_RENDERED_PREV_KEY = "_editor_rendered_prev"


def start_editor_run(data: SyllabusData) -> None:
    """Call once at the top of every run, before any editor widget is created.

    `data` (the SyllabusData in session state) is the master copy of every
    field. Only boxes that were on screen in the previous run hold fresh
    values, so only those are copied into `data`. Boxes of the hidden page
    are left alone: Streamlit may already have dropped or blanked them.
    """
    previous = set(st.session_state.get(_RENDERED_NOW_KEY, ()))
    st.session_state[_RENDERED_PREV_KEY] = previous
    st.session_state[_RENDERED_NOW_KEY] = set()
    for field_name in previous:
        value = _read_widget(field_name)
        if value is not _MISSING:
            setattr(data, field_name, data_value_from_widget(field_name, value))


def sync_data_from_editor_state(data: SyllabusData) -> None:
    """Copy values of editors drawn so far in this run into the dataclass."""
    for field_name in st.session_state.get(_RENDERED_NOW_KEY, ()):
        value = _read_widget(field_name)
        if value is not _MISSING:
            setattr(data, field_name, data_value_from_widget(field_name, value))


_MISSING = object()


def _read_widget(field_name: str) -> Any:
    """Fresh value of a field's editor, in the editor's form, or _MISSING."""
    if field_name == UNIT_FIELD:
        units = _read_units_panel()
        return _MISSING if units is None else block_from_units(units)
    if field_name in BOOK_FIELDS:
        books = _read_books(field_name)
        return _MISSING if books is None else "\n".join(books)
    key = editor_key(field_name)
    return st.session_state[key] if key in st.session_state else _MISSING


def _refresh_widget(field_name: str) -> None:
    """Load a field's editor from `data` (callbacks / before drawing only)."""
    if field_name == UNIT_FIELD:
        _reset_units_panel()
    elif field_name in BOOK_FIELDS:
        _reset_books(field_name)
    else:
        st.session_state[editor_key(field_name)] = widget_value_from_data(
            st.session_state.data, field_name
        )


def _prepare_editor_widget(field_name: str) -> str:
    """Return the widget key, filling the box from `data` only when needed.

    A value is pushed into a box only if that box was NOT on screen in the
    previous run (first load, page switch, reset). Pushing values into a
    box that is already shown makes Streamlit overwrite whatever the user is
    typing there, which made edits "change back".
    """
    key = editor_key(field_name)
    previous = st.session_state.get(_RENDERED_PREV_KEY, set())
    if field_name not in previous or key not in st.session_state:
        st.session_state[key] = widget_value_from_data(st.session_state.data, field_name)
    st.session_state[_RENDERED_NOW_KEY].add(field_name)
    return key


def clear_generated_pdfs() -> None:
    for state_key in PDF_STATE_KEYS:
        st.session_state.pop(state_key, None)


def reset_editor_state() -> None:
    st.session_state.data = default_data()
    clear_generated_pdfs()
    for field_name in syllabus_field_names():
        _refresh_widget(field_name)
    # Clear the uploader widget too, otherwise its retained file would be
    # re-extracted on the next rerun and immediately undo the reset.
    st.session_state.pop("pdf_import", None)
    st.session_state.pop("pdf_import_file_id", None)
    st.session_state.pop("pdf_import_summary", None)
    st.session_state.pop("pdf_import_number_warning", None)
    st.session_state.pop("draft_import", None)
    st.session_state.pop("draft_import_file_id", None)
    st.session_state.pop("draft_import_message", None)


def apply_extracted_fields(extracted: dict[str, Any]) -> None:
    """Push values parsed from an uploaded PDF into the dataclass and widget state."""
    data: SyllabusData = st.session_state.data
    for field_name, value in extracted.items():
        setattr(data, field_name, value)
        _refresh_widget(field_name)
    clear_generated_pdfs()


def unreadable_numbers(extracted: dict[str, Any]) -> list[str]:
    """Number fields whose imported text has no number in it (e.g. 'NA')."""
    return [
        f"{_field_label(name)} ('{value}')"
        for name, value in extracted.items()
        if name in NUMBER_FIELDS and str(value).strip() and parse_number(str(value)) is None
    ]


def text_input(label: str, field_name: str, **kwargs: Any) -> str:
    key = _prepare_editor_widget(field_name)
    return st.text_input(label, key=key, on_change=_on_editor_change, args=(field_name,), **kwargs)


def text_area(label: str, field_name: str, **kwargs: Any) -> str:
    key = _prepare_editor_widget(field_name)
    return st.text_area(
        label,
        key=key,
        on_change=_on_editor_change,
        args=(field_name,),
        **kwargs,
    )


def checkbox(label: str, field_name: str, **kwargs: Any) -> bool:
    key = _prepare_editor_widget(field_name)
    return st.checkbox(label, key=key, on_change=clear_generated_pdfs, **kwargs)


# ---------------------------------------------------------------------------
# Units editor: one panel per unit
# ---------------------------------------------------------------------------
_UNIT_IDS_KEY = "_unit_ids"
_UNIT_NEXT_ID_KEY = "_unit_next_id"
_UNIT_STYLE_KEY = "unit_heading_style"
_UNIT_STYLE_LAST_KEY = "_unit_heading_style_last"
_UNIT_DELETED_KEY = "_unit_last_deleted"
_UNIT_PASTE_KEY = "_units_paste"
_STYLE_LABELS = {"UNIT": "UNIT I, UNIT II, …", "Phase": "Phase I, Phase II, …", "Custom": "My own headings"}


def _unit_keys(uid: int) -> tuple[str, str]:
    return f"unit_title__{uid}", f"unit_body__{uid}"


def _new_unit_id() -> int:
    uid = st.session_state.get(_UNIT_NEXT_ID_KEY, 0)
    st.session_state[_UNIT_NEXT_ID_KEY] = uid + 1
    return uid


def _clean_body_lines(body: str) -> str:
    """Drop blank lines (they would split a unit in the text form)."""
    return "\n".join(line.rstrip() for line in str(body).splitlines() if line.strip())


def _units_panel_ready() -> bool:
    ids = st.session_state.get(_UNIT_IDS_KEY)
    if ids is None or _UNIT_STYLE_KEY not in st.session_state:
        return False
    return all(key in st.session_state for uid in ids for key in _unit_keys(uid))


def _read_units_panel() -> list[tuple[str, str]] | None:
    """Units as (heading, body) from the panels, or None if not available."""
    if not _units_panel_ready():
        return None
    style = st.session_state[_UNIT_STYLE_KEY]
    units = []
    for position, uid in enumerate(st.session_state[_UNIT_IDS_KEY], start=1):
        title_key, body_key = _unit_keys(uid)
        heading = compose_heading(style, position, str(st.session_state[title_key]))
        units.append((heading, _clean_body_lines(st.session_state[body_key])))
    return units


def _reset_units_panel() -> None:
    """Rebuild all panels from data.units with fresh widget keys."""
    for uid in st.session_state.get(_UNIT_IDS_KEY, []):
        for key in _unit_keys(uid):
            st.session_state.pop(key, None)
    units = st.session_state.data.units
    style = detect_heading_style(units)
    st.session_state[_UNIT_STYLE_KEY] = style
    st.session_state[_UNIT_STYLE_LAST_KEY] = style
    ids = []
    for heading, body in units:
        uid = _new_unit_id()
        title_key, body_key = _unit_keys(uid)
        st.session_state[title_key] = panel_title(style, heading)
        st.session_state[body_key] = body
        ids.append(uid)
    st.session_state[_UNIT_IDS_KEY] = ids


def _units_changed() -> None:
    """After a panel action: copy panels into data, drop generated PDFs."""
    units = _read_units_panel()
    if units is not None:
        st.session_state.data.units = units
    clear_generated_pdfs()


def _move_unit(uid: int, step: int) -> None:
    ids = st.session_state[_UNIT_IDS_KEY]
    i = ids.index(uid)
    j = i + step
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    _units_changed()


def _delete_unit(uid: int) -> None:
    ids = st.session_state[_UNIT_IDS_KEY]
    title_key, body_key = _unit_keys(uid)
    st.session_state[_UNIT_DELETED_KEY] = {
        "position": ids.index(uid),
        "title": st.session_state.pop(title_key, ""),
        "body": st.session_state.pop(body_key, ""),
    }
    ids.remove(uid)
    _units_changed()


def _restore_unit() -> None:
    deleted = st.session_state.pop(_UNIT_DELETED_KEY, None)
    if not deleted:
        return
    uid = _new_unit_id()
    title_key, body_key = _unit_keys(uid)
    st.session_state[title_key] = deleted["title"]
    st.session_state[body_key] = deleted["body"]
    ids = st.session_state[_UNIT_IDS_KEY]
    ids.insert(min(deleted["position"], len(ids)), uid)
    _units_changed()


def _add_unit() -> None:
    uid = _new_unit_id()
    title_key, body_key = _unit_keys(uid)
    st.session_state[title_key] = ""
    st.session_state[body_key] = ""
    st.session_state[_UNIT_IDS_KEY].append(uid)
    _units_changed()


def _on_unit_style_change() -> None:
    """Keep headings meaningful when switching UNIT / Phase / own headings."""
    old = st.session_state.get(_UNIT_STYLE_LAST_KEY, "UNIT")
    new = st.session_state[_UNIT_STYLE_KEY]
    for position, uid in enumerate(st.session_state.get(_UNIT_IDS_KEY, []), start=1):
        title_key, _ = _unit_keys(uid)
        title = str(st.session_state.get(title_key, ""))
        if new == "Custom" and old != "Custom":
            st.session_state[title_key] = compose_heading(old, position, title)
        elif new != "Custom":
            # Drop any "UNIT I:" / "Phase I:" prefix; the new one is added automatically.
            found, bare = split_heading(title)
            st.session_state[title_key] = bare if found != "Custom" else title
    st.session_state[_UNIT_STYLE_LAST_KEY] = new
    _units_changed()


def _on_unit_edit(uid: int) -> None:
    """A unit's title or contents changed; auto-correct just that unit.

    Only this unit's boxes are rewritten, so typing in other units is never
    disturbed. Undo brings back exactly what the user typed.
    """
    _units_changed()
    typed_block = _current_text(UNIT_FIELD)  # panels hold the raw edit here
    if not st.session_state.get(AUTO_GRAMMAR_KEY):
        return
    title_key, body_key = _unit_keys(uid)
    title = tidy_title(str(st.session_state.get(title_key, "")))
    body = tidy_unit_body(str(st.session_state.get(body_key, "")))
    fixes = 0
    warning = None
    if body:
        if _load_grammar_tool() is None:
            warning = ("Grammar was skipped: the grammar engine did not start. "
                       "Lines and spaces were still fixed.")
        else:
            try:
                matches, corrected = _check_text(body)
                if matches:
                    body, fixes = tidy_unit_body(corrected), len(matches)
            except Exception as exc:
                warning = f"Grammar check failed: {exc}"
    if title != st.session_state.get(title_key):
        st.session_state[title_key] = title
    if body != _clean_body_lines(st.session_state.get(body_key, "")):
        st.session_state[body_key] = body
    _units_changed()
    new_block = _current_text(UNIT_FIELD)
    notice: dict[str, Any] = {"label": "Units", "changes": {}, "grammar_fixes": fixes}
    if new_block != typed_block:
        notice["changes"][UNIT_FIELD] = (typed_block, new_block)
    if warning:
        notice["warning"] = warning
    if notice["changes"] or warning:
        st.session_state[_AUTO_NOTICE_KEY] = notice


def _apply_units_paste() -> None:
    text = str(st.session_state.get(_UNIT_PASTE_KEY, "")).strip()
    if not text:
        st.session_state[_AUTO_NOTICE_KEY] = {
            "label": "Paste all units", "changes": {}, "warning": "The paste box is empty.",
        }
        return
    old_block = _current_text(UNIT_FIELD)
    new_block = tidy_units(text, join_wrapped=True)
    _set_text(UNIT_FIELD, new_block)
    st.session_state[_UNIT_PASTE_KEY] = ""
    st.session_state[_AUTO_NOTICE_KEY] = {
        "label": "Paste all units",
        "changes": {UNIT_FIELD: (old_block, _current_text(UNIT_FIELD))},
        "grammar_fixes": 0,
    }
    clear_generated_pdfs()


def render_units_editor() -> None:
    previous = st.session_state.get(_RENDERED_PREV_KEY, set())
    if UNIT_FIELD not in previous or not _units_panel_ready():
        _reset_units_panel()
    st.session_state[_RENDERED_NOW_KEY].add(UNIT_FIELD)

    st.markdown("**Units**")
    st.radio(
        "Heading style",
        HEADING_STYLES,
        key=_UNIT_STYLE_KEY,
        horizontal=True,
        format_func=_STYLE_LABELS.get,
        on_change=_on_unit_style_change,
        help="UNIT/Phase numbers are added automatically. Choose 'My own headings' "
        "to type each heading in full.",
    )
    style = st.session_state[_UNIT_STYLE_KEY]
    ids = list(st.session_state[_UNIT_IDS_KEY])
    for position, uid in enumerate(ids, start=1):
        title_key, body_key = _unit_keys(uid)
        with st.container(border=True):
            c_label, c_title, c_up, c_down, c_del = st.columns([1.3, 7, 0.6, 0.6, 0.6])
            with c_label:
                label = compose_heading(style, position, "").rstrip(":") if style != "Custom" else f"#{position}"
                st.markdown(f"**{label}**")
            with c_title:
                st.text_input(
                    "Title",
                    key=title_key,
                    label_visibility="collapsed",
                    placeholder="Full heading" if style == "Custom" else "Unit title, e.g. Fluid Dynamics",
                    on_change=_on_unit_edit,
                    args=(uid,),
                )
            with c_up:
                st.button("↑", key=f"unit_up__{uid}", on_click=_move_unit, args=(uid, -1),
                          disabled=position == 1, help="Move up", width="stretch")
            with c_down:
                st.button("↓", key=f"unit_down__{uid}", on_click=_move_unit, args=(uid, 1),
                          disabled=position == len(ids), help="Move down", width="stretch")
            with c_del:
                st.button("🗑", key=f"unit_del__{uid}", on_click=_delete_unit, args=(uid,),
                          help="Delete this unit (you can restore it)", width="stretch")
            st.text_area(
                "Contents",
                key=body_key,
                label_visibility="collapsed",
                height=110,
                placeholder="Contents of this unit",
                on_change=_on_unit_edit,
                args=(uid,),
            )

    col_add, col_restore = st.columns([1, 1])
    with col_add:
        st.button("➕ Add unit", key="unit_add", on_click=_add_unit, width="stretch")
    with col_restore:
        deleted = st.session_state.get(_UNIT_DELETED_KEY)
        if deleted:
            name = deleted["title"] or "the deleted unit"
            st.button(f"↩️ Restore {name}"[:60], key="unit_restore", on_click=_restore_unit,
                      width="stretch")

    with st.expander("📋 Paste all units at once (from Word or an old syllabus)"):
        st.caption(
            "Paste every unit here and click the button: the text is split into "
            "panels automatically. Lines starting with 'Unit 1', 'UNIT II:' etc. "
            "start a new unit. This replaces the current units (you can Undo)."
        )
        st.text_area("Paste units", key=_UNIT_PASTE_KEY, height=200, label_visibility="collapsed",
                     placeholder="UNIT I: Introduction\nProperties of fluids…\n\nUNIT II: Fluid Kinematics\n…")
        st.button("Replace units with pasted text", key="unit_paste_apply", on_click=_apply_units_paste)


# ---------------------------------------------------------------------------
# Books editor: 3-row table
# ---------------------------------------------------------------------------
BOOK_FIELDS = ("textbooks", "reference_books")
_BOOK_COLUMNS = ("Author(s)", "Book title", "Publisher")


def _books_version_key(field_name: str) -> str:
    return f"_books_version__{field_name}"


def _books_widget_key(field_name: str) -> str:
    return f"books__{field_name}__{st.session_state.get(_books_version_key(field_name), 0)}"


def _books_base_key(field_name: str) -> str:
    return f"_books_base__{field_name}"


def _books_check_key(field_name: str) -> str:
    return f"_books_check__{field_name}"


def _reset_books(field_name: str) -> None:
    """Rebuild a book table from data with a fresh widget key."""
    st.session_state.pop(_books_widget_key(field_name), None)
    st.session_state[_books_version_key(field_name)] = (
        st.session_state.get(_books_version_key(field_name), 0) + 1
    )
    lines = list(getattr(st.session_state.data, field_name))
    rows = [dict(zip(_BOOK_COLUMNS, parse_book(line))) for line in lines]
    while len(rows) < MAX_BOOKS:
        rows.append(dict.fromkeys(_BOOK_COLUMNS, ""))
    st.session_state[_books_base_key(field_name)] = rows
    st.session_state[_books_check_key(field_name)] = {
        index for index, line in enumerate(lines) if book_needs_check(line)
    }


def _book_rows(field_name: str) -> list[dict] | None:
    """Table rows with the user's edits applied, or None if not available."""
    base = st.session_state.get(_books_base_key(field_name))
    if base is None:
        return None
    rows = [dict(row) for row in base]
    state = st.session_state.get(_books_widget_key(field_name)) or {}
    for index, changes in (state.get("edited_rows") or {}).items():
        index = int(index)
        if 0 <= index < len(rows):
            rows[index].update({k: ("" if v is None else str(v)) for k, v in changes.items()})
    return rows


def _read_books(field_name: str) -> list[str] | None:
    rows = _book_rows(field_name)
    if rows is None:
        return None
    lines = [format_book(*(row.get(col, "") for col in _BOOK_COLUMNS)) for row in rows]
    return [line for line in lines if line]


def _on_books_change(field_name: str) -> None:
    books = _read_books(field_name)
    if books is not None:
        setattr(st.session_state.data, field_name, books)
    # A row the user has edited no longer needs the "please check" mark.
    state = st.session_state.get(_books_widget_key(field_name)) or {}
    edited = {int(i) for i in (state.get("edited_rows") or {})}
    st.session_state[_books_check_key(field_name)] = (
        st.session_state.get(_books_check_key(field_name), set()) - edited
    )
    clear_generated_pdfs()


def render_books_editor(field_name: str, label: str) -> None:
    previous = st.session_state.get(_RENDERED_PREV_KEY, set())
    if field_name not in previous or _books_base_key(field_name) not in st.session_state:
        _reset_books(field_name)
    st.session_state[_RENDERED_NOW_KEY].add(field_name)

    st.markdown(f"**{label}** (max {MAX_BOOKS})")
    st.data_editor(
        pd.DataFrame(st.session_state[_books_base_key(field_name)], columns=list(_BOOK_COLUMNS)),
        key=_books_widget_key(field_name),
        num_rows="fixed",
        hide_index=True,
        width="stretch",
        on_change=_on_books_change,
        args=(field_name,),
        column_config={
            "Author(s)": st.column_config.TextColumn(width="medium", help="e.g. Thomas H. Cormen"),
            "Book title": st.column_config.TextColumn(width="large", help="e.g. Introduction to Algorithms"),
            "Publisher": st.column_config.TextColumn(
                width="small", help="e.g. PHI — 'Pub.' is added automatically"
            ),
        },
    )
    books = _read_books(field_name) or []
    if books:
        st.caption("In the PDF: " + " · ".join(f"{i}. {line}" for i, line in enumerate(books, 1)))
    if len(getattr(st.session_state.data, field_name)) > MAX_BOOKS:
        st.warning(f"More than {MAX_BOOKS} entries were imported; only {MAX_BOOKS} are allowed.")
    to_check = sorted(st.session_state.get(_books_check_key(field_name), set()))
    if to_check:
        st.warning(
            "Row(s) " + ", ".join(str(i + 1) for i in to_check) + " were split into columns "
            "automatically from old text with extra commas. Please check that author, "
            "title and publisher are in the right columns."
        )


# ---------------------------------------------------------------------------
# Number boxes: credits, hours, marks
# ---------------------------------------------------------------------------
_NUMBER_UNITS = {"credits": "", "count": "", "hours": " (hours)", "marks": " (marks)"}


def number_input(label: str, field_name: str) -> float:
    kind = NUMBER_FIELDS[field_name]
    key = _prepare_editor_widget(field_name)
    return st.number_input(
        label + _NUMBER_UNITS[kind],
        key=key,
        min_value=0.0,
        max_value=NUMBER_MAX[kind],
        step=NUMBER_STEP[kind],
        format="%g",
        on_change=clear_generated_pdfs,
        help=f"Shown in the PDF as '{format_number(kind, 3 if kind != 'marks' else 40)}'.",
    )


def render_marks_total(internal_field: str, end_field: str) -> None:
    internal = float(st.session_state.get(editor_key(internal_field)) or 0)
    end = float(st.session_state.get(editor_key(end_field)) or 0)
    st.caption(f"Total marks: **{internal + end:g}** (internal {internal:g} + end semester {end:g})")


def render_theory_warnings(data: SyllabusData) -> None:
    """Soft checks that the structured editors cannot enforce themselves."""
    if not data.units:
        st.warning("There are no units. Click **➕ Add unit** or paste them.")


def render_grammar_panel(page_name: str) -> None:
    """Render the grammar check & fix expander for the given editor page."""
    st.divider()
    with st.expander("🔍 Grammar Check & Fix", expanded=False):
        if not _GRAMMAR_AVAILABLE:
            st.error(
                "`language_tool_python` is not installed. "
                "Run `pip install language_tool_python` and restart the app."
            )
            return

        st.caption(
            "Checks spelling and grammar in text fields using LanguageTool. "
            "Uses the local engine if Java is installed, otherwise the free public "
            "LanguageTool service (text is sent to languagetool.org, with rate limits)."
        )

        results_key = _grammar_results_key(page_name)

        col_check, col_clear = st.columns([1, 1])
        with col_check:
            run_check = st.button(
                "Check Grammar",
                key=f"_grammar_run_{page_name}",
                type="primary",
                width="stretch",
            )
        with col_clear:
            st.button(
                "Clear Results",
                key=f"_grammar_clear_{page_name}",
                width="stretch",
                on_click=_clear_grammar_results,
                args=(page_name,),
            )

        if run_check:
            st.session_state.pop(f"_grammar_skipped_{page_name}", None)
            tool = _load_grammar_tool()
            if tool is None:
                st.error(
                    "Grammar engine failed to start, and the public LanguageTool "
                    "service could not be reached. Install **Java** or check the "
                    "internet connection, then click Check Grammar again."
                )
            else:
                results: dict[str, dict] = {}
                fields_to_check = _GRAMMAR_FIELDS.get(page_name, [])
                try:
                    with st.spinner("Checking grammar…"):
                        for field_name in fields_to_check:
                            text = _current_text(field_name)
                            matches, corrected = _check_text(text)
                            if matches:
                                results[field_name] = {
                                    "original": text,
                                    "corrected": corrected,
                                    "matches": matches,
                                }
                except Exception as exc:
                    st.error(f"Grammar check failed: {exc}")
                else:
                    st.session_state[results_key] = results

        skipped = st.session_state.get(f"_grammar_skipped_{page_name}")
        if skipped:
            st.warning(
                "Not applied because the text was edited after the check: "
                + ", ".join(skipped)
                + ". Click **Check Grammar** again."
            )

        results = st.session_state.get(results_key)
        if results is None:
            return

        if not results:
            st.success("✅ No grammar issues found in any field.")
            return

        total = sum(len(r["matches"]) for r in results.values())
        st.warning(f"Found **{total}** issue(s) across **{len(results)}** field(s).")

        apply_all_fields = [f for f in results if f not in _APPLY_ALL_EXCLUDED]
        if apply_all_fields:
            st.button(
                "✅ Apply All Fixes (book lists excluded)",
                key=f"_grammar_apply_all_{page_name}",
                type="primary",
                on_click=_apply_grammar_fixes,
                args=(page_name, apply_all_fields),
                help="Book lists contain author names that are often flagged wrongly. "
                "Review and apply those one field at a time below.",
            )

        st.divider()

        for field_name, data in results.items():
            label = _GRAMMAR_LABELS.get(field_name, field_name)
            n = len(data["matches"])
            with st.expander(f"📝 **{label}** — {n} issue(s)", expanded=True):
                for i, m in enumerate(data["matches"], start=1):
                    st.markdown(f"**Issue {i}:** {m['message']}")
                    st.code(_context_with_marker(m), language=None)
                    if m["replacements"]:
                        suggestions = " · ".join(f"`{r}`" for r in m["replacements"])
                        st.markdown(f"💡 **Suggestion(s):** {suggestions}")
                    if i < n:
                        st.divider()

                st.markdown("**Corrected text preview:**")
                st.text_area(
                    "Corrected",
                    value=data["corrected"],
                    height=120,
                    disabled=True,
                    key=f"_grammar_preview_{field_name}",
                    label_visibility="collapsed",
                )
                st.button(
                    f"Apply fixes to {label}",
                    key=f"_grammar_apply_{field_name}",
                    on_click=_apply_grammar_fixes,
                    args=(page_name, [field_name]),
                )


def render_signatory_controls(page_name: str, checkbox_attr: str) -> None:
    st.divider()
    st.subheader(f"{page_name} Signatories")
    st.caption("Turn signatories on or off for this page only. The labels below are reused in both page templates.")
    checkbox(f"Show signatories at the bottom of the {page_name.lower()} PDF", checkbox_attr)

    sig1, sig2, sig3 = st.columns(3)
    with sig1:
        text_input("Signature 1", "bos_chairperson")
    with sig2:
        text_input("Signature 2", "dean_academic")
    with sig3:
        text_input("Signature 3", "principal")


def render_export_panel(
    page_name: str, latex_content: str, file_stem: str, signed: bool, download_stem: str
) -> None:
    """file_stem is internal (state keys, TeX job name); download_stem names the files."""
    st.subheader(f"{page_name} Downloads")
    st.caption("Generate this page only. Theory and Lab downloads are separate and do not include each other.")

    pdf_state_key = f"{file_stem}_pdf_bytes"
    # Different names so the signed and unsigned PDFs don't overwrite each other.
    pdf_name = f"{download_stem}_{'with' if signed else 'without'}_signature.pdf"
    preview_tab, latex_tab = st.tabs(["Generate & Download", "LaTeX Source"])

    with preview_tab:
        st.download_button(
            f"Download {page_name} LaTeX",
            data=latex_content,
            file_name=f"{download_stem}.tex",
            mime="text/plain",
            key=f"download_{file_stem}_latex_direct",
        )

        st.caption(
            ":red[**Two PDFs are required:** one with and one without the signature box.] "
            "Generate and download once, then switch the signatory checkbox above and repeat."
        )
        if st.button(f"Generate {page_name} PDF", type="primary", key=f"generate_{file_stem}_pdf"):
            try:
                with st.spinner(
                    "Generating PDF… (the first time, Times New Roman is downloaded; this can take a minute)"
                ):
                    pdf_bytes, _ = compile_pdf(latex_content, output_stem=file_stem)
                st.session_state[pdf_state_key] = pdf_bytes
                st.success(f"{page_name} PDF generated successfully.")
            except Exception as exc:
                st.session_state.pop(pdf_state_key, None)
                st.error("PDF generation failed.")
                st.code(str(exc))

        if pdf_state_key in st.session_state:
            st.download_button(
                f"Download {page_name} PDF ({'with' if signed else 'without'} signatures)",
                data=st.session_state[pdf_state_key],
                file_name=pdf_name,
                mime="application/pdf",
                key=f"download_{file_stem}_pdf",
            )

    with latex_tab:
        st.text_area("Generated LaTeX", value=latex_content, height=500, disabled=True)


if "data" not in st.session_state:
    st.session_state.data = default_data()


data: SyllabusData = st.session_state.data
PAGE_KEY = "editor_page"
_PAGE_LAST_KEY = "_editor_page_last"
_PAGE_SCROLL_KEY = "_editor_page_scroll_top"
THEORY_PAGE, LAB_PAGE = "Theory Page", "Lab Page"
_PAGE_LABELS = {THEORY_PAGE: "📘 Theory page", LAB_PAGE: "🧪 Lab page"}


def _on_page_change() -> None:
    # Clicking the selected button again would leave no page selected: keep the old one.
    if st.session_state.get(PAGE_KEY) not in _PAGE_LABELS:
        st.session_state[PAGE_KEY] = st.session_state.get(_PAGE_LAST_KEY, THEORY_PAGE)
    st.session_state[_PAGE_LAST_KEY] = st.session_state[PAGE_KEY]


def _go_to_page(page: str) -> None:
    st.session_state[PAGE_KEY] = page
    st.session_state[_PAGE_LAST_KEY] = page
    st.session_state[_PAGE_SCROLL_KEY] = True


def render_page_switch() -> str:
    """Big Theory/Lab switch at the top of the page. Returns the open page."""
    if st.session_state.get(PAGE_KEY) not in _PAGE_LABELS:
        st.session_state[PAGE_KEY] = st.session_state.get(_PAGE_LAST_KEY, THEORY_PAGE)
    extra = {"required": True} if "required" in inspect.signature(st.segmented_control).parameters else {}
    st.segmented_control(
        "Which page are you editing?",
        list(_PAGE_LABELS),
        key=PAGE_KEY,
        format_func=_PAGE_LABELS.get,
        on_change=_on_page_change,
        width="stretch",
        **extra,
    )
    page = st.session_state.get(PAGE_KEY) or THEORY_PAGE
    other = LAB_PAGE if page == THEORY_PAGE else THEORY_PAGE
    st.caption(
        f"You are editing the **{_PAGE_LABELS[page]}**. The {_PAGE_LABELS[other]} is "
        "separate and gets its own PDF. Save draft, PDF import and Auto-correct are in "
        "the sidebar (on a phone, tap **»** at the top-left to open it)."
    )
    if st.session_state.pop(_PAGE_SCROLL_KEY, False):
        _scroll_to_top()
    return page


_SCROLL_JS = """
const doc = (window.parent && window.parent.document) || document;
for (const sel of ['[data-testid="stMain"]', '[data-testid="stAppViewContainer"]', 'section.main']) {
  const el = doc.querySelector(sel); if (el) { el.scrollTo({top: 0}); }
}
(window.parent || window).scrollTo({top: 0});
"""


def _scroll_to_top() -> None:
    """After Next/Back at the bottom of a page, jump to the top of the new page.

    Uses st.html with JavaScript (runs in the page) where available; older
    Streamlit versions fall back to a zero-height component iframe.
    """
    try:
        if "unsafe_allow_javascript" in inspect.signature(st.html).parameters:
            st.html(f"<script>{_SCROLL_JS}</script>", unsafe_allow_javascript=True)
            return
        if hasattr(st, "iframe"):
            st.iframe(f"<script>{_SCROLL_JS}</script>", height=1)
            return
        import streamlit.components.v1 as components  # removed in newer Streamlit

        components.html(f"<script>{_SCROLL_JS}</script>", height=0)
    except Exception:
        pass  # scrolling is a convenience; never break the page for it


def render_page_heading(page: str) -> None:
    if page == THEORY_PAGE:
        st.header("📘 Theory page", divider="blue")
        st.caption("Step 1 of 2 — course details, objectives, outcomes, units and books.")
    else:
        st.header("🧪 Lab page", divider="green")
        st.caption("Step 2 of 2 — lab details, objectives, outcomes and experiments. "
                   "It is not added to the Theory PDF.")


def render_page_footer_nav(page: str) -> None:
    st.divider()
    if page == THEORY_PAGE:
        st.button("Next: 🧪 Lab page →", key="nav_to_lab", on_click=_go_to_page,
                  args=(LAB_PAGE,), type="primary", width="stretch")
    else:
        st.button("← Back to 📘 Theory page", key="nav_to_theory", on_click=_go_to_page,
                  args=(THEORY_PAGE,), width="stretch")


start_editor_run(data)

with st.sidebar:
    st.header("Tools")
    st.caption("Switch between the **Theory** and **Lab** pages with the big buttons at the top of the page.")
    if st.button("Reset to default"):
        reset_editor_state()
        st.rerun()

    st.divider()
    st.subheader("Save / Load Draft")
    st.caption(
        "Your work is **lost** if the page is refreshed or the app goes to sleep. "
        "Click **Save draft** often; later, load the file to continue where you stopped."
    )
    st.download_button(
        "💾 Save draft",
        data=draft_to_json(data),
        file_name=draft_file_name(data),
        mime="application/json",
        key="draft_save",
        width="stretch",
        help="Downloads a small .json file with every field on both pages.",
    )
    uploaded_draft = st.file_uploader("Load draft (.json)", type=["json"], key="draft_import")
    if uploaded_draft is not None and st.session_state.get("draft_import_file_id") != uploaded_draft.file_id:
        st.session_state["draft_import_file_id"] = uploaded_draft.file_id
        try:
            loaded = draft_from_json(uploaded_draft.getvalue())
        except DraftError as exc:
            st.session_state["draft_import_message"] = ("error", str(exc))
        else:
            apply_extracted_fields(loaded)
            if st.session_state.get(AUTO_GRAMMAR_KEY):
                with st.spinner("Correcting the draft…"):
                    clean_fields(
                        [f for f in loaded if f in _TEXT_FIELDS],
                        join_wrapped=True, grammar=True, label="Loaded draft",
                    )
            st.session_state["draft_import_message"] = (
                "success",
                f"Draft loaded: {len(loaded)} field(s) restored.",
            )
            st.rerun()
    draft_message = st.session_state.get("draft_import_message")
    if draft_message:
        kind, text = draft_message
        (st.success if kind == "success" else st.error)(text)

    st.divider()
    st.subheader("Import from PDF")
    st.caption(
        "Upload an existing syllabus PDF to auto-fill the fields. "
        "Unit-based and Project/Internship weekly-plan syllabi fill the Theory "
        "page; lab syllabi are detected automatically and fill the Lab page. "
        "Broken lines, extra spaces and numbering are fixed automatically "
        "(grammar too, when Auto-correct is on). Review the values before generating."
    )
    uploaded_pdf = st.file_uploader("Upload syllabus PDF", type=["pdf"], key="pdf_import")
    if uploaded_pdf is not None and st.session_state.get("pdf_import_file_id") != uploaded_pdf.file_id:
        st.session_state["pdf_import_file_id"] = uploaded_pdf.file_id
        try:
            extracted = extract_syllabus_fields(uploaded_pdf.getvalue())
        except PdfExtractionError as exc:
            st.session_state.pop("pdf_import_summary", None)
            st.error(str(exc))
        else:
            if extracted:
                st.session_state["pdf_import_number_warning"] = unreadable_numbers(extracted)
                apply_extracted_fields(extracted)
                with st.spinner("Correcting the imported text…"):
                    clean_fields(
                        [f for f in extracted if f in _TEXT_FIELDS],
                        join_wrapped=True,
                        grammar=bool(st.session_state.get(AUTO_GRAMMAR_KEY)),
                        label="PDF import",
                    )
                st.session_state["pdf_import_summary"] = sorted(extracted.keys())
                st.rerun()
            else:
                st.session_state.pop("pdf_import_summary", None)
                st.warning("No recognisable syllabus fields were found in that PDF.")

    if st.session_state.get("pdf_import_number_warning"):
        st.warning(
            "No number found for: " + ", ".join(st.session_state["pdf_import_number_warning"])
            + ". Set these by hand."
        )
    if st.session_state.get("pdf_import_summary"):
        filled = st.session_state["pdf_import_summary"]
        st.success(
            f"Filled {len(filled)} field(s) from the PDF: "
            + ", ".join(name.replace('_', ' ') for name in filled)
        )

    st.divider()
    st.subheader("Auto-correct")
    st.toggle(
        "Auto-correct (grammar, lines & spaces)",
        key=AUTO_GRAMMAR_KEY,
        help="When on: every box is corrected as soon as you finish editing it, and "
        "imported PDFs and loaded drafts are corrected too. Fixes extra spaces, "
        "spaces around commas/brackets, broken lines, typed numbering (1., CO1:, •), "
        "unit headings (Unit 3 - x → UNIT III: X) and, for text boxes, grammar. "
        "Text and Reference Books get spacing fixes only (author names are often "
        "flagged as spelling errors). Every change has an Undo button.",
    )
    if not _GRAMMAR_AVAILABLE:
        st.caption("Grammar engine not installed: only lines and spaces will be fixed.")
    st.caption("To fix text already in the boxes, use **✨ Clean up all fields** on the page.")


current_page = render_page_switch()

st.info(
    "Your edits are saved automatically when you move to another field, click "
    "anywhere outside the box, or press a button such as Generate. In multi-line "
    "boxes you can also press Ctrl/Cmd+Enter to save without leaving the box.",
    icon="💾",
)
st.button(
    "✨ Clean up all fields (grammar, lines & spaces)",
    key="_clean_all_fields",
    on_click=_clean_all_fields,
    help="Fixes every field on both pages, including text from old PDF imports and "
    "drafts. Book lists get spacing fixes only. You can Undo.",
)
render_auto_correct_notice()

if current_page == THEORY_PAGE:
    render_page_heading(THEORY_PAGE)
    text_input("Course Title", "title")
    text_input("Subject Code", "subject_code")
    meta1, meta2 = st.columns(2)
    with meta1:
        number_input("Total Credits", "total_credits")
        number_input("Teaching Hours / Week", "teaching_hours")
        number_input("Tutorial Hours / Week", "tutorial_hours")
        number_input("Practical Hours / Week", "practical_hours")

    with meta2:
        number_input("Exam Duration", "exam_duration")
        number_input("Internal Evaluation", "internal_evaluation")
        number_input("End Semester Evaluation", "end_semester_evaluation")
        render_marks_total("internal_evaluation", "end_semester_evaluation")

    text_area("Course Objectives", "objectives", height=140,
              help="One objective per line. Numbering is added in the PDF.")
    text_area("Course Outcomes", "outcomes", height=180,
              help="One outcome per line. CO1, CO2, … are added in the PDF.")
    render_units_editor()
    render_books_editor("textbooks", "Text Books")
    render_books_editor("reference_books", "Reference Books")

    sync_data_from_editor_state(data)
    render_theory_warnings(data)
    render_grammar_panel("Theory Page")
    render_signatory_controls("Theory", "include_theory_signature_box")
    sync_data_from_editor_state(data)
    render_export_panel(
        "Theory",
        render_theory_latex(data),
        "theory_syllabus",
        data.include_theory_signature_box,
        subject_file_stem(data.subject_code, data.title, "theory_syllabus"),
    )
    render_page_footer_nav(THEORY_PAGE)

else:
    render_page_heading(LAB_PAGE)
    text_input("Lab Table Title", "lab_table_title")
    text_input("Lab Section Title", "lab_section_title")
    text_input("Lab Subject Code", "lab_subject_code")
    lab1, lab2 = st.columns(2)
    with lab1:
        number_input("Lab Credits", "lab_total_credits")
        number_input("Lab Teaching Hours / Week", "lab_teaching_hours")
        number_input("Lab Tutorial Hours / Week", "lab_tutorial_hours")
        number_input("Lab Practical Hours / Week", "lab_practical_hours")
    with lab2:
        number_input("Lab Exam Duration", "lab_exam_duration")
        number_input("Lab Internal Evaluation", "lab_internal_evaluation")
        number_input("Lab End Semester Evaluation", "lab_end_semester_evaluation")
        render_marks_total("lab_internal_evaluation", "lab_end_semester_evaluation")

    text_area("Lab Objectives", "lab_objectives", height=140)
    text_area("Lab Outcomes", "lab_outcomes", height=180)
    text_input("Experiments Heading", "experiments_heading")
    text_area("Experiments", "experiments", height=400)

    render_grammar_panel("Lab Page")
    render_signatory_controls("Lab", "include_lab_signature_box")
    sync_data_from_editor_state(data)
    render_export_panel(
        "Lab",
        render_lab_latex(data),
        "lab_syllabus",
        data.include_lab_signature_box,
        subject_file_stem(data.lab_subject_code, data.lab_table_title, "lab_syllabus"),
    )
    render_page_footer_nav(LAB_PAGE)
