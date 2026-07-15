from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import List


@dataclass
class SyllabusData:
    title: str = "Fluid Mechanics"
    total_credits: str = "03"
    subject_code: str = "PCCCE5T001"
    teaching_hours: str = "02"
    tutorial_hours: str = "01"
    practical_hours: str = "00"
    exam_duration: str = "03 Hours"
    internal_evaluation: str = "40 Marks"
    end_semester_evaluation: str = "60 Marks"
    objectives: List[str] = field(default_factory=lambda: [
        "To impart fundamental knowledge of fluid properties, fluid statics, kinematics, dynamics, and the application of fluid mechanics in civil engineering systems.",
        "To apply theoretical concepts in analyzing fluid flow and selecting appropriate measurement techniques and model laws.",
    ])
    outcomes: List[str] = field(default_factory=lambda: [
        "Explain fluid properties and fundamental principles like buoyancy and stability of bodies.",
        "Analyze fluid pressure and compute hydrostatic forces on submerged surfaces.",
        "Apply continuity, Euler's, and Bernoulli's equations to solve fluid motion problems.",
        "Analyze and select flow measuring devices for pipelines, tanks, and open channels.",
        "Apply dimensional analysis and model laws in hydraulic model studies.",
        "Evaluate real-world civil engineering problems involving fluid flow using theoretical approaches.",
    ])
    units: List[tuple[str, str]] = field(default_factory=lambda: [
        (
            "UNIT I: Introduction and Archimedes Principle",
            "Introduction: Basic Concepts and Definitions, Distinction between a fluid and a solid; Density, Specific weight, Specific gravity, Kinematic and dynamic viscosity; variation of viscosity with temperature, Newton’s law of viscosity; Classification of fluids.\n Fluid Statics: Fluid Pressure: Pressure at a point, Pascal’s law, Piezometer, U-Tube Manometer, Differential Manometer.",
        ),
        (
            "UNIT II: Fluid Kinematics",
            "Classification of flows, stream line, streak line, path line, continuity equation.",
        ),
        (
            "UNIT III: Fluid Dynamics",
            "Surface and body forces - Euler’s and Bernoulli’s equations for flow along a stream line, momentum equation and its applications.",
        ),
        (
            "UNIT IV: Boundary Layer Concepts",
            "Definition, thicknesses, characteristics along thin plate, laminar and turbulent boundary layers.",
        ),
        (
            "UNIT V: Flow Through Pipes",
            "Reynolds experiment, Darcy Weisbach equation, Minor losses in pipes, pipes in series and pipes in parallel, total energy line-hydraulic gradient line.",
        ),
    ])
    textbooks: List[str] = field(default_factory=lambda: [
        "R.K.Bansal, A Text Book of Fluid Mechanics and Hydraulic Machines, Laxmi  Pub.",
        "P.N. Modi & S.M. Set, Hydraulics, Fluid Mechanics and Hydraulic Machines, Standard Book House, Pub.",
    ])
    reference_books: List[str] = field(default_factory=lambda: [
        "F.M. White, Fluid Mechanics, Mc. Graw Hill, Pub.",
        "S. Ramamrutham, Hydraulics, Fluid Mechanics and Fluid Machines, Dhanpat Rai Pub.",
    ])
    # Kept for older saved Streamlit sessions; Theory and Lab are now exported independently.
    include_lab_content: bool = False
    lab_table_title: str = "Fluid Mechanics Lab"
    lab_section_title: str = "FLUID MECHANICS LAB"
    lab_total_credits: str = "01"
    lab_subject_code: str = "PCCCE5L001"
    lab_teaching_hours: str = "00"
    lab_tutorial_hours: str = "00"
    lab_practical_hours: str = "02"
    lab_exam_duration: str = "03 Hours"
    lab_internal_evaluation: str = "25 Marks"
    lab_end_semester_evaluation: str = "25 Marks"
    lab_objectives: List[str] = field(default_factory=lambda: [
        "To verify fluid mechanics principles through hands-on experiments.",
        "To develop skills in using flow-measurement and hydraulic laboratory equipment.",
    ])
    lab_outcomes: List[str] = field(default_factory=lambda: [
        "Conduct laboratory experiments related to fluid statics and fluid flow.",
        "Measure discharge, pressure, and flow coefficients using standard equipment.",
        "Analyze experimental observations and compare them with theoretical principles.",
        "Prepare technical laboratory reports with calculations and conclusions.",
    ])
    experiments_heading: str = "Perform any 08 Experiments."
    experiments: List[str] = field(default_factory=lambda: [
        "Determination of Metacentric height and its importance.",
        "Verification of Bernoulli's Theorem.",
        "Calibration of Venturimeter.",
        "Calibration of Orifice meter.",
        "To determine the coefficient of discharge of Venturimeter.",
        "To determine the coefficient of discharge of Orifice meter.",
        "Calibration of Rectangular Notches/ V-Notches.",
        "Hydraulic Coefficients of an orifice.",
        "Hydraulic Coefficients of a Mouthpiece.",
        "Impact of jet apparatus.",
    ])
    include_signature_box: bool = True
    include_theory_signature_box: bool = True
    include_lab_signature_box: bool = True
    bos_chairperson: str = "BOS Chairperson"
    dean_academic: str = "Dean (Academic)"
    principal: str = "Principal"


def _escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def _latex_lines(items: List[str], prefix: str) -> str:
    rows: List[str] = []
    for index, item in enumerate(items, start=1):
        clean = item.strip()
        if clean:
            rows.append(f"        {prefix}{index} & {_escape(clean)} " + r"\\")
            rows.append("        \\hline")
    return "\n".join(rows)


def _latex_enumerate(items: List[str]) -> str:
    rows: List[str] = []
    for item in items:
        clean = item.strip()
        if clean:
            rows.append(f"    \\item {_escape(clean)}")
    return "\n".join(rows)


# Covers 1-20 (i..xx); regex backtracking resolves overlaps like "vi" vs "v".
_ROMAN_MARKER = r"(?:i{1,3}|iv|v|vi{1,3}|ix|x{1,2}|xi{1,3}|xiv|xv|xvi{1,3}|xix)"
# enumitem lists give automatic hanging indentation: wrapped lines align under
# the item text, never under the label. leftmargin=* lets enumitem pick a tidy
# indent and nested lists step in a further consistent amount.
_PLAN_L1_BEGIN = r"\begin{enumerate}[label=\alph*., leftmargin=*, itemsep=3pt, topsep=4pt, parsep=0pt]"
_PLAN_L2_BEGIN = r"\begin{enumerate}[label=\roman*., leftmargin=*, itemsep=2pt, topsep=2pt, parsep=0pt]"


def _render_plan_body(lines: List[str]) -> str:
    """Render a unit body as nested lists when it uses a./i. markers, else prose.

    Letter markers (``a.`` ``b.``) become the outer list, roman markers
    (``i.`` ``ii.``) a nested inner list, and ``(Maps to ...)`` notes hang under
    the current item. Lines without a marker are emitted as plain paragraphs, so
    ordinary unit-based syllabi are unaffected.
    """
    parts: List[str] = []
    level1_open = False
    level2_open = False

    def close_level2() -> None:
        nonlocal level2_open
        if level2_open:
            parts.append(r"\end{enumerate}")
            level2_open = False

    def close_all() -> None:
        nonlocal level1_open
        close_level2()
        if level1_open:
            parts.append(r"\end{enumerate}")
            level1_open = False

    for line in lines:
        if line.startswith(r"\noindent"):
            close_all()
            parts.append(line + r"\par")
            continue

        roman = re.match(rf"^{_ROMAN_MARKER}[.)]\s+(.*)$", line, re.IGNORECASE)
        letter = re.match(r"^[a-z][.)]\s+(.*)$", line, re.IGNORECASE)

        if roman:
            if not level1_open:
                parts.append(_PLAN_L1_BEGIN)
                level1_open = True
                parts.append(r"\item\leavevmode")
            if not level2_open:
                parts.append(_PLAN_L2_BEGIN)
                level2_open = True
            parts.append(r"\item " + _escape(roman.group(1).strip()))
        elif letter:
            close_level2()
            if not level1_open:
                parts.append(_PLAN_L1_BEGIN)
                level1_open = True
            parts.append(r"\item " + _escape(letter.group(1).strip()))
        elif level1_open or level2_open:
            # A (Maps to ...) note or a stray continuation: break under the item.
            parts.append(r"\\ " + _escape(line))
        else:
            parts.append(r"\noindent " + _escape(line) + r"\par")

    close_all()
    return "\n".join(parts)


def _latex_units(units: List[tuple[str, str]]) -> str:
    blocks: List[str] = []
    for heading, body in units:
        heading_clean = _escape(heading.strip())
        visible_lines = [line.strip() for line in body.splitlines() if line.strip()]
        body_tex = _render_plan_body(visible_lines)
        blocks.append("\\subsection*{" + heading_clean + "}\n" + body_tex)
    return "\n\n\\medskip\n\n".join(blocks)


def _signature_footer(data: SyllabusData, include_signatures: bool) -> str:
    if not include_signatures:
        return ""

    return rf"""\fancyfoot[C]{{
    \small
    \begin{{tabular}}{{|m{{0.3\textwidth}}|m{{0.3\textwidth}}|m{{0.3\textwidth}}|}}
        \hline
        & & \\
        & & \\
        & & \\
        \hline
        \centering \textbf{{{_escape(data.bos_chairperson)}}} & \centering \textbf{{{_escape(data.dean_academic)}}} & \centering \textbf{{{_escape(data.principal)}}} \tabularnewline
        \hline
    \end{{tabular}}
}}"""


def _document(content: str, data: SyllabusData, include_signatures: bool) -> str:
    footer_block = _signature_footer(data, include_signatures)
    return rf"""\documentclass[12pt, a4paper]{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage{{iftex}}
\ifPDFTeX
\PackageError{{syllabus-generator}}{{This template requires XeLaTeX or LuaLaTeX for Times New Roman}}{{Use xelatex or lualatex to compile this document.}}
\else
\usepackage{{fontspec}}
\setmainfont{{Times New Roman}}
\fi
\usepackage[margin=1in, bottom=1.5in]{{geometry}}
\usepackage[table]{{xcolor}}
\usepackage{{array}}
\usepackage{{enumitem}}
\usepackage{{titlesec}}
\usepackage{{fancyhdr}}
\usepackage{{tabularx}}
\usepackage{{needspace}}

\titleformat{{\section}}
  {{\normalfont\fontsize{{12}}{{14}}\bfseries\centering}}{{\thesection}}{{1em}}{{}}
\titleformat{{\subsection}}
  {{\normalfont\fontsize{{12}}{{14}}\bfseries}}{{\thesubsection}}{{1em}}{{}}
\titlespacing*{{\section}}{{0pt}}{{12pt}}{{6pt}}
\titlespacing*{{\subsection}}{{0pt}}{{10pt}}{{4pt}}

\definecolor{{headerblue}}{{RGB}}{{30, 50, 100}}
\definecolor{{tableorange}}{{RGB}}{{255, 218, 185}}
\definecolor{{tablepurple}}{{RGB}}{{230, 210, 240}}
\definecolor{{outcomen-green}}{{RGB}}{{210, 230, 180}}
\definecolor{{contentblue}}{{RGB}}{{60, 80, 155}}
\definecolor{{contentpurple}}{{RGB}}{{190, 90, 220}}

\pagestyle{{fancy}}
\fancyhf{{}}
\renewcommand{{\headrulewidth}}{{0pt}}
{footer_block}

\begin{{document}}
{content}
\end{{document}}
"""


def _course_detail_table(
    title: str,
    total_credits: str,
    subject_code: str,
    teaching_hours: str,
    tutorial_hours: str,
    practical_hours: str,
    exam_duration: str,
    internal_evaluation: str,
    end_semester_evaluation: str,
) -> str:
    return rf"""\begin{{center}}
    \renewcommand{{\arraystretch}}{{1.5}}
    \begin{{tabularx}}{{\textwidth}}{{|>{{\hsize=.85\hsize\linewidth=\hsize}}X|>{{\hsize=1.15\hsize\linewidth=\hsize}}X|}}
        \hline
        \rowcolor{{tableorange}} \multicolumn{{2}}{{|c|}}{{\textbf{{{_escape(title)}}}}} \\
        \hline
        Total Credits : {_escape(total_credits)} & Subject Code : {_escape(subject_code)} \\
        \hline
        Teaching Scheme & Examination Scheme \\
        \hline
        Teaching Hrs/Week : {_escape(teaching_hours)} & Duration of End Semester Exam : {_escape(exam_duration)} \\
        \hline
        Tutorials Hrs/Week : {_escape(tutorial_hours)} & Internal Evaluation \hspace{{2.4cm}}: {_escape(internal_evaluation)} \\
        \hline
        Practical Hrs/Week : {_escape(practical_hours)} & End Semester Examination \hspace{{1cm}}: {_escape(end_semester_evaluation)} \\
        \hline
    \end{{tabularx}}
\end{{center}}"""


def _objectives_outcomes(objectives: List[str], outcomes: List[str]) -> str:
    objective_rows = _latex_lines(objectives, "")
    outcome_rows = _latex_lines(outcomes, "CO")
    return rf"""\vspace{{1cm}}
\begin{{center}}
    \renewcommand{{\arraystretch}}{{1.5}}
    \begin{{tabularx}}{{\textwidth}}{{|c|X|}}
        \hline
        \rowcolor{{tablepurple}} \multicolumn{{2}}{{|c|}}{{\textbf{{Course Objectives}}}} \\
        \hline
{objective_rows}
    \end{{tabularx}}
\end{{center}}

\vspace{{1cm}}
\begin{{center}}
    \renewcommand{{\arraystretch}}{{1.5}}
    \begin{{tabularx}}{{\textwidth}}{{|c|X|}}
        \hline
        \rowcolor{{outcomen-green}} \multicolumn{{2}}{{|c|}}{{\textbf{{Course Outcomes}}}} \\
        \hline
        \multicolumn{{2}}{{|l|}}{{\textbf{{After completion of syllabus, students would be able to}}}} \\
        \hline
{outcome_rows}
    \end{{tabularx}}
\end{{center}}"""


def _book_list(label: str, items: List[str]) -> str:
    rows = _latex_enumerate(items)
    if not rows.strip():
        return ""
    return rf"""\noindent \textbf{{{label}}}
\begin{{enumerate}}[label=\arabic*., leftmargin=*, itemsep=2pt, parsep=0pt, topsep=2pt, partopsep=0pt]
{rows}
\end{{enumerate}}"""


def _books_section(data: SyllabusData) -> str:
    text_books = _book_list("Text books:", data.textbooks)
    reference_books = _book_list("Reference books:", data.reference_books)
    blocks = [block for block in (text_books, reference_books) if block]
    if not blocks:
        # Project/Internship syllabi carry no books; emit nothing so the page
        # is just the content plus the signatory box.
        return ""

    body = "\n\\vspace{\\baselineskip}\n\n".join(blocks)
    return rf"""
\Needspace{{12\baselineskip}}
\begin{{samepage}}
\vspace{{1cm}}
{body}
\end{{samepage}}"""


def _render_theory_content(data: SyllabusData) -> str:
    units = _latex_units(data.units)

    return rf"""% --- THEORY PAGE: COURSE DETAILS & OBJECTIVES ---
{_course_detail_table(data.title, data.total_credits, data.subject_code, data.teaching_hours, data.tutorial_hours, data.practical_hours, data.exam_duration, data.internal_evaluation, data.end_semester_evaluation)}
{_objectives_outcomes(data.objectives, data.outcomes)}

\newpage
% --- THEORY PAGE: COURSE CONTENT ---
\begin{{center}}
\textbf{{\color{{contentblue}}\fontsize{{16}}{{18}}\selectfont {_escape(data.title.upper())}}}\par
\vspace{{0.3cm}}
\textbf{{\color{{contentpurple}}\fontsize{{14}}{{16}}\selectfont COURSE CONTENT}}
\end{{center}}

\vspace{{0.15cm}}
{units}
{_books_section(data)}"""


def _render_lab_content(data: SyllabusData) -> str:
    experiment_rows = _latex_enumerate(data.experiments)
    if experiment_rows:
        # An enumerate with no \item is a LaTeX error, so only emit the
        # environment when at least one experiment line is present.
        experiments = rf"""\begin{{enumerate}}[label=\arabic*.]
{experiment_rows}
\end{{enumerate}}"""
    else:
        experiments = ""

    return rf"""% --- LAB PAGE: COURSE DETAILS & OBJECTIVES ---
{_course_detail_table(data.lab_table_title, data.lab_total_credits, data.lab_subject_code, data.lab_teaching_hours, data.lab_tutorial_hours, data.lab_practical_hours, data.lab_exam_duration, data.lab_internal_evaluation, data.lab_end_semester_evaluation)}
{_objectives_outcomes(data.lab_objectives, data.lab_outcomes)}

\newpage
% --- LAB PAGE: EXPERIMENTS ---
\begin{{center}}
\textbf{{\color{{contentblue}}\fontsize{{16}}{{18}}\selectfont {_escape(data.lab_section_title.upper())}}}\par
\end{{center}}
\vspace{{1cm}}
\noindent \textbf{{{_escape(data.experiments_heading)}}}

{experiments}
\vspace{{\baselineskip}}

\noindent * Above is the recommended list but not limited to this"""


def render_theory_latex(data: SyllabusData) -> str:
    return _document(_render_theory_content(data), data, data.include_theory_signature_box)


def render_lab_latex(data: SyllabusData) -> str:
    return _document(_render_lab_content(data), data, data.include_lab_signature_box)


def render_latex(data: SyllabusData) -> str:
    """Backward-compatible alias for the independently exported theory page."""
    return render_theory_latex(data)


def _run_tex(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def compile_pdf(latex_content: str, output_stem: str = "syllabus") -> tuple[bytes, str]:
    """Compile via a Unicode engine so Times New Roman is honored.

    The LaTeX template uses fontspec with \setmainfont{Times New Roman}, which
    requires XeLaTeX or LuaLaTeX.
    """
    have_latexmk = shutil.which("latexmk") is not None
    have_xelatex = shutil.which("xelatex") is not None
    have_lualatex = shutil.which("lualatex") is not None
    if not (have_latexmk or have_xelatex or have_lualatex):
        raise RuntimeError(
            "No compatible TeX compiler found. Please install a TeX distribution "
            "(e.g. MiKTeX or TeX Live) that provides latexmk with XeLaTeX, xelatex, "
            "or lualatex."
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        tex_path = temp_path / f"{output_stem}.tex"
        tex_path.write_text(latex_content, encoding="utf-8")
        pdf_path = temp_path / f"{output_stem}.pdf"
        log = ""

        if have_latexmk:
            result = _run_tex(
                [
                    "latexmk",
                    "-xelatex",
                    "-interaction=nonstopmode",
                    "-file-line-error",
                    tex_path.name,
                ],
                temp_path,
            )
            if result.returncode == 0 and pdf_path.exists():
                return pdf_path.read_bytes(), latex_content
            log = (result.stdout or "") + "\n" + (result.stderr or "")

        engine = "xelatex" if have_xelatex else "lualatex"
        if have_xelatex or have_lualatex:
            # Two passes so cross-page layout settles; enough for this document.
            for _ in range(2):
                result = _run_tex(
                    [engine, "-interaction=nonstopmode", "-file-line-error", tex_path.name],
                    temp_path,
                )
            if result.returncode == 0 and pdf_path.exists():
                return pdf_path.read_bytes(), latex_content
            log = (result.stdout or "") + "\n" + (result.stderr or "")

        raise RuntimeError(log.strip() or "PDF compilation failed.")
