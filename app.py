from __future__ import annotations

import re
from dataclasses import fields
from typing import Any

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
from text_cleanup import clean_field_text

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
        key = editor_key(field_name)
        if st.session_state.get(key) == result["original"]:
            st.session_state[key] = result["corrected"]
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
    f.name for f in fields(SyllabusData) if not isinstance(getattr(SyllabusData(), f.name), bool)
]


def _field_label(field_name: str) -> str:
    return _GRAMMAR_LABELS.get(field_name, field_name.replace("_", " ").title())


def _current_text(field_name: str) -> str:
    """Latest text of a field.

    Inside a button/edit callback, boxes drawn in the last run hold the
    newest text. Otherwise the master copy in `data` is up to date.
    """
    key = editor_key(field_name)
    if field_name in st.session_state.get(_RENDERED_NOW_KEY, set()) and key in st.session_state:
        return str(st.session_state[key])
    return str(widget_value_from_data(st.session_state.data, field_name))


def _set_text(field_name: str, text: str) -> None:
    """Write a field to both the master copy and its box.

    Only call from a callback or before the box is drawn in this run.
    """
    setattr(st.session_state.data, field_name, data_value_from_widget(field_name, text))
    st.session_state[editor_key(field_name)] = text


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
                  use_container_width=True)
    with col_ok:
        st.button("OK", key="_auto_grammar_ok", use_container_width=True,
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
    if field_name in TEXT_LIST_FIELDS:
        return "\n".join(value)
    if field_name == UNIT_FIELD:
        return block_from_units(value)
    return value


def data_value_from_widget(field_name: str, value: Any) -> Any:
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
        key = editor_key(field_name)
        if key in st.session_state:
            setattr(data, field_name, data_value_from_widget(field_name, st.session_state[key]))


def sync_data_from_editor_state(data: SyllabusData) -> None:
    """Copy values of boxes drawn so far in this run into the dataclass."""
    for field_name in st.session_state.get(_RENDERED_NOW_KEY, ()):
        key = editor_key(field_name)
        if key in st.session_state:
            setattr(data, field_name, data_value_from_widget(field_name, st.session_state[key]))


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
        st.session_state.pop(editor_key(field_name), None)
    # Clear the uploader widget too, otherwise its retained file would be
    # re-extracted on the next rerun and immediately undo the reset.
    st.session_state.pop("pdf_import", None)
    st.session_state.pop("pdf_import_file_id", None)
    st.session_state.pop("pdf_import_summary", None)
    st.session_state.pop("draft_import", None)
    st.session_state.pop("draft_import_file_id", None)
    st.session_state.pop("draft_import_message", None)


def apply_extracted_fields(extracted: dict[str, Any]) -> None:
    """Push values parsed from an uploaded PDF into the dataclass and widget state."""
    data: SyllabusData = st.session_state.data
    for field_name, value in extracted.items():
        setattr(data, field_name, value)
        st.session_state[editor_key(field_name)] = widget_value_from_data(data, field_name)
    clear_generated_pdfs()


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


# "UNIT III: Title" for theory syllabi; "Phase I: ..." for Project/Internship weekly plans.
_UNIT_HEADING_RE = re.compile(r"^(?:UNIT\s+[IVXL]+\s*:|PHASE\b)", re.IGNORECASE)
MAX_BOOKS = 3


def render_theory_warnings(data: SyllabusData) -> None:
    """Soft checks for the formats the field labels ask for."""
    bad_headings = [heading for heading, _ in data.units if not _UNIT_HEADING_RE.match(heading)]
    if bad_headings:
        st.warning(
            "These unit headings do not follow the format `UNIT III: Title` "
            "(or a blank line is missing between units): "
            + "; ".join(f"`{h}`" for h in bad_headings)
        )
    for label, items in (("Text Books", data.textbooks), ("Reference Books", data.reference_books)):
        if len(items) > MAX_BOOKS:
            st.warning(f"{label}: {len(items)} entries found, maximum is {MAX_BOOKS}.")


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
                use_container_width=True,
            )
        with col_clear:
            st.button(
                "Clear Results",
                key=f"_grammar_clear_{page_name}",
                use_container_width=True,
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
                            text = str(st.session_state.get(editor_key(field_name), ""))
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
start_editor_run(data)

with st.sidebar:
    st.header("Actions")
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
        use_container_width=True,
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

    st.divider()
    current_page = st.radio(
        "Editor Pages",
        ["Theory Page", "Lab Page"],
        help="Each page is edited, generated, and downloaded separately.",
    )

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

if current_page == "Theory Page":
    st.subheader("Theory Details")
    text_input("Course Title", "title")
    meta1, meta2 = st.columns(2)
    with meta1:
        text_input("Total Credits", "total_credits")
        text_input("Teaching Hours / Week", "teaching_hours")
        text_input("Tutorial Hours / Week", "tutorial_hours")
        text_input("Practical Hours / Week", "practical_hours")

    with meta2:
        text_input("Subject Code", "subject_code")
        text_input("Exam Duration", "exam_duration")
        text_input("Internal Evaluation", "internal_evaluation")
        text_input("End Semester Evaluation", "end_semester_evaluation")

    text_area("Course Objectives", "objectives", height=140)
    text_area("Course Outcomes", "outcomes", height=180)
    text_area(
        "Units (give one line space for new unit. "
        ":red[Use this format only] :blue[UNIT III: Fluid Dynamics]:red[)]",
        "units",
        height=600,
        help="Saved when you click outside the box or press Ctrl/Cmd+Enter.",
    )
    text_area(
        "Text Books :red[(Max 3. Strictly follow the Sample:] "
        ":blue[Thomas H. Cormen, Introduction to Algorithms, PHI Pub.]:red[)]",
        "textbooks",
        height=110,
    )
    text_area(
        "Reference Books :red[(Max 3. Strictly follow the Sample:] "
        ":blue[Parag Himanshu Dave, Balchandra Dave, Design and Analysis of Algorithms, Education, O'Reilly Pub.]:red[)]",
        "reference_books",
        height=110,
    )

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

else:
    st.subheader("Lab Details")
    st.caption("The Lab page is independent. It will not be added to the Theory PDF.")
    text_input("Lab Table Title", "lab_table_title")
    text_input("Lab Section Title", "lab_section_title")
    lab1, lab2 = st.columns(2)
    with lab1:
        text_input("Lab Credits", "lab_total_credits")
        text_input("Lab Teaching Hours / Week", "lab_teaching_hours")
        text_input("Lab Tutorial Hours / Week", "lab_tutorial_hours")
        text_input("Lab Practical Hours / Week", "lab_practical_hours")
    with lab2:
        text_input("Lab Subject Code", "lab_subject_code")
        text_input("Lab Exam Duration", "lab_exam_duration")
        text_input("Lab Internal Evaluation", "lab_internal_evaluation")
        text_input("Lab End Semester Evaluation", "lab_end_semester_evaluation")

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
