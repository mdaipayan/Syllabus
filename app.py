from __future__ import annotations

import re
from dataclasses import fields
from typing import Any

import streamlit as st

from pdf_extractor import PdfExtractionError, extract_syllabus_fields
from syllabus_generator import SyllabusData, compile_pdf, render_lab_latex, render_theory_latex

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
# Fields auto-correct may change: every grammar-checked field except book lists.
_AUTO_GRAMMAR_FIELDS = {
    name
    for names in _GRAMMAR_FIELDS.values()
    for name in names
    if name not in _APPLY_ALL_EXCLUDED
}


def _on_editor_change(field_name: str) -> None:
    """on_change callback for editor widgets.

    Runs before the script redraws the widgets, so it may replace the text.
    With auto-correct on, the edited field is grammar-checked and fixed
    straight away, and the original is kept so the user can undo.
    """
    clear_generated_pdfs()
    if not st.session_state.get(AUTO_GRAMMAR_KEY) or field_name not in _AUTO_GRAMMAR_FIELDS:
        return
    key = editor_key(field_name)
    original = str(st.session_state.get(key, ""))
    if _load_grammar_tool() is None:
        st.session_state[_AUTO_NOTICE_KEY] = {
            "error": "the grammar engine did not start (no Java, and the public "
            "LanguageTool service could not be reached)."
        }
        return
    try:
        matches, corrected = _check_text(original)
    except Exception as exc:
        st.session_state[_AUTO_NOTICE_KEY] = {"error": str(exc)}
        return
    if not matches or corrected == original:
        return
    st.session_state[key] = corrected
    st.session_state[_AUTO_NOTICE_KEY] = {
        "field": field_name,
        "original": original,
        "corrected": corrected,
        "count": len(matches),
    }


def _undo_auto_correct() -> None:
    notice = st.session_state.pop(_AUTO_NOTICE_KEY, None)
    # Only undo if the box still holds what auto-correct wrote.
    if notice and "field" in notice:
        key = editor_key(notice["field"])
        if st.session_state.get(key) == notice["corrected"]:
            st.session_state[key] = notice["original"]
    clear_generated_pdfs()


def render_auto_correct_notice() -> None:
    notice = st.session_state.get(_AUTO_NOTICE_KEY)
    if not notice:
        return
    if "error" in notice:
        st.warning(f"Auto-correct could not run: {notice['error']}")
        st.session_state.pop(_AUTO_NOTICE_KEY, None)
        return
    label = _GRAMMAR_LABELS.get(notice["field"], notice["field"])
    col_msg, col_undo, col_ok = st.columns([6, 1, 1])
    with col_msg:
        st.success(f"✨ Auto-corrected **{notice['count']}** issue(s) in **{label}**.")
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


def initialize_editor_state(data: SyllabusData) -> None:
    """Prime Streamlit widget state once so reruns never overwrite in-progress edits."""
    for field_name in syllabus_field_names():
        key = editor_key(field_name)
        if key not in st.session_state:
            st.session_state[key] = widget_value_from_data(data, field_name)


def sync_data_from_editor_state(data: SyllabusData) -> None:
    """Copy committed widget state into the dataclass used by the LaTeX renderer."""
    for field_name in syllabus_field_names():
        key = editor_key(field_name)
        if key in st.session_state:
            setattr(data, field_name, data_value_from_widget(field_name, st.session_state[key]))


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


def apply_extracted_fields(extracted: dict[str, Any]) -> None:
    """Push values parsed from an uploaded PDF into the dataclass and widget state."""
    data: SyllabusData = st.session_state.data
    for field_name, value in extracted.items():
        setattr(data, field_name, value)
        st.session_state[editor_key(field_name)] = widget_value_from_data(data, field_name)
    clear_generated_pdfs()


def keep_editor_state() -> None:
    """Stop Streamlit from deleting the state of widgets not drawn in this run.

    Streamlit removes a keyed widget's value when a rerun does not draw that
    widget (e.g. the Theory/Lab page the user isn't viewing). Re-assigning each
    key at the top of the script turns it into normal session state, so it
    survives page switches. Must run before any editor widget is created.
    """
    for field_name in syllabus_field_names():
        key = editor_key(field_name)
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def text_input(label: str, field_name: str, **kwargs: Any) -> str:
    return st.text_input(label, key=editor_key(field_name), on_change=clear_generated_pdfs, **kwargs)


def text_area(label: str, field_name: str, **kwargs: Any) -> str:
    return st.text_area(
        label,
        key=editor_key(field_name),
        on_change=_on_editor_change,
        args=(field_name,),
        **kwargs,
    )


def checkbox(label: str, field_name: str, **kwargs: Any) -> bool:
    return st.checkbox(label, key=editor_key(field_name), on_change=clear_generated_pdfs, **kwargs)


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


def render_export_panel(page_name: str, latex_content: str, file_stem: str, signed: bool) -> None:
    st.subheader(f"{page_name} Downloads")
    st.caption("Generate this page only. Theory and Lab downloads are separate and do not include each other.")

    pdf_state_key = f"{file_stem}_pdf_bytes"
    # Different names so the signed and unsigned PDFs don't overwrite each other.
    pdf_name = f"{file_stem}_{'with' if signed else 'without'}_signature.pdf"
    preview_tab, latex_tab = st.tabs(["Generate & Download", "LaTeX Source"])

    with preview_tab:
        st.download_button(
            f"Download {page_name} LaTeX",
            data=latex_content,
            file_name=f"{file_stem}.tex",
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
keep_editor_state()
initialize_editor_state(data)
sync_data_from_editor_state(data)

with st.sidebar:
    st.header("Actions")
    if st.button("Reset to default"):
        reset_editor_state()
        st.rerun()

    st.divider()
    st.subheader("Import from PDF")
    st.caption(
        "Upload an existing syllabus PDF to auto-fill the fields. "
        "Unit-based and Project/Internship weekly-plan syllabi fill the Theory "
        "page; lab syllabi are detected automatically and fill the Lab page. "
        "Review the extracted values before generating."
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
    st.subheader("Grammar")
    if _GRAMMAR_AVAILABLE:
        st.toggle(
            "Auto-correct grammar",
            key=AUTO_GRAMMAR_KEY,
            help="When on, a text box is checked and corrected as soon as you finish "
            "editing it (click outside or press Ctrl/Cmd+Enter). An Undo button "
            "appears after each change. Text and Reference Books are never "
            "auto-corrected, because author names are often flagged wrongly.",
        )
        if st.session_state.get(AUTO_GRAMMAR_KEY):
            st.caption(
                "Applies to new edits only. To fix text already in the boxes "
                "(e.g. from a PDF import), use **Grammar Check & Fix** on the page."
            )
    else:
        st.caption("Grammar tools are unavailable (`language_tool_python` not installed).")

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
        "Theory", render_theory_latex(data), "theory_syllabus", data.include_theory_signature_box
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
    render_export_panel("Lab", render_lab_latex(data), "lab_syllabus", data.include_lab_signature_box)
