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


@st.cache_resource(show_spinner="Loading grammar engine… (first run may take a minute)")
def _load_grammar_tool() -> "language_tool_python.LanguageTool | None":
    if not _GRAMMAR_AVAILABLE:
        return None
    try:
        return language_tool_python.LanguageTool("en-US")
    except Exception:
        return None


def _check_text(text: str) -> tuple[list[dict], str]:
    """Return (matches_info, corrected_text). Returns empty list on failure."""
    tool = _load_grammar_tool()
    if tool is None or not text.strip():
        return [], text
    matches = tool.check(text)
    if not matches:
        return [], text
    corrected = language_tool_python.utils.correct(text, matches)
    info = [
        {
            "message": m.message,
            "context": m.context,
            "offset": m.offset,
            "length": m.errorLength,
            "replacements": m.replacements[:4],
            "rule": m.ruleId,
        }
        for m in matches
    ]
    return info, corrected


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


def text_input(label: str, field_name: str, **kwargs: Any) -> str:
    # Streamlit drops a keyed widget's bound value whenever a rerun does not
    # instantiate that widget (e.g. the Theory/Lab page the user isn't
    # viewing), so it renders blank the next time it reappears even though
    # session_state still has the right value. Passing `value=` explicitly
    # re-seeds it; `key=` still wins on reruns where the user has typed a
    # different value, so in-progress edits are unaffected.
    key = editor_key(field_name)
    kwargs.setdefault("value", st.session_state.get(key))
    return st.text_input(label, key=key, on_change=clear_generated_pdfs, **kwargs)


def text_area(label: str, field_name: str, **kwargs: Any) -> str:
    key = editor_key(field_name)
    kwargs.setdefault("value", st.session_state.get(key))
    return st.text_area(label, key=key, on_change=clear_generated_pdfs, **kwargs)


def checkbox(label: str, field_name: str, **kwargs: Any) -> bool:
    key = editor_key(field_name)
    kwargs.setdefault("value", st.session_state.get(key))
    return st.checkbox(label, key=key, on_change=clear_generated_pdfs, **kwargs)


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
            "Java must be installed on this machine for the local engine to start."
        )

        results_key = f"_grammar_results_{page_name}"

        col_check, col_clear = st.columns([1, 1])
        with col_check:
            run_check = st.button(
                "Check Grammar",
                key=f"_grammar_run_{page_name}",
                type="primary",
                use_container_width=True,
            )
        with col_clear:
            if st.button(
                "Clear Results",
                key=f"_grammar_clear_{page_name}",
                use_container_width=True,
            ):
                st.session_state.pop(results_key, None)
                st.rerun()

        if run_check:
            tool = _load_grammar_tool()
            if tool is None:
                st.error(
                    "Grammar engine failed to start. "
                    "Make sure **Java** is installed and on your PATH, then restart the app."
                )
            else:
                results: dict[str, dict] = {}
                fields_to_check = _GRAMMAR_FIELDS.get(page_name, [])
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
                st.session_state[results_key] = results

        results = st.session_state.get(results_key)
        if results is None:
            return

        if not results:
            st.success("✅ No grammar issues found in any field.")
            return

        total = sum(len(r["matches"]) for r in results.values())
        st.warning(f"Found **{total}** issue(s) across **{len(results)}** field(s).")

        # Apply-all button at the top for convenience.
        if st.button(
            "✅ Apply All Fixes",
            key=f"_grammar_apply_all_{page_name}",
            type="primary",
        ):
            for field_name, data in results.items():
                st.session_state[editor_key(field_name)] = data["corrected"]
            st.session_state.pop(results_key, None)
            clear_generated_pdfs()
            st.rerun()

        st.divider()

        for field_name, data in results.items():
            label = _GRAMMAR_LABELS.get(field_name, field_name)
            n = len(data["matches"])
            with st.expander(f"📝 **{label}** — {n} issue(s)", expanded=True):
                for i, m in enumerate(data["matches"], start=1):
                    st.markdown(f"**Issue {i}:** {m['message']}")
                    # Show context with the error portion highlighted.
                    ctx = m["context"]
                    st.code(ctx, language=None)
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
                if st.button(
                    f"Apply fixes to {label}",
                    key=f"_grammar_apply_{field_name}",
                ):
                    st.session_state[editor_key(field_name)] = data["corrected"]
                    del st.session_state[results_key][field_name]
                    clear_generated_pdfs()
                    st.rerun()


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


def render_export_panel(page_name: str, latex_content: str, file_stem: str) -> None:
    st.subheader(f"{page_name} Downloads")
    st.caption("Generate this page only. Theory and Lab downloads are separate and do not include each other.")

    pdf_state_key = f"{file_stem}_pdf_bytes"
    preview_tab, latex_tab = st.tabs(["Generate & Download", "LaTeX Source"])

    with preview_tab:
        st.download_button(
            f"Download {page_name} LaTeX",
            data=latex_content,
            file_name=f"{file_stem}.tex",
            mime="text/plain",
            key=f"download_{file_stem}_latex_direct",
        )

        if st.button(f"Generate {page_name} PDF", type="primary", key=f"generate_{file_stem}_pdf"):
            try:
                pdf_bytes, _ = compile_pdf(latex_content, output_stem=file_stem)
                st.session_state[pdf_state_key] = pdf_bytes
                st.success(f"{page_name} PDF generated successfully.")
            except Exception as exc:
                st.session_state.pop(pdf_state_key, None)
                st.error("PDF generation failed.")
                st.code(str(exc))

        if pdf_state_key in st.session_state:
            st.download_button(
                f"Download {page_name} PDF :red[**(2 PDF required with and without signature box)**]",
                data=st.session_state[pdf_state_key],
                file_name=f"{file_stem}.pdf",
                mime="application/pdf",
                key=f"download_{file_stem}_pdf",
            )

    with latex_tab:
        st.text_area("Generated LaTeX", value=latex_content, height=500, disabled=True)


if "data" not in st.session_state:
    st.session_state.data = default_data()


data: SyllabusData = st.session_state.data
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
    render_grammar_panel("Theory Page")
    render_signatory_controls("Theory", "include_theory_signature_box")
    sync_data_from_editor_state(data)
    render_export_panel("Theory", render_theory_latex(data), "theory_syllabus")

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

    sync_data_from_editor_state(data)
    render_grammar_panel("Lab Page")
    render_signatory_controls("Lab", "include_lab_signature_box")
    sync_data_from_editor_state(data)
    render_export_panel("Lab", render_lab_latex(data), "lab_syllabus")
