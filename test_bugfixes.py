"""Regression tests for syllabus_generator, pdf_extractor, and app helpers.

Run with:  python -m unittest test_bugfixes -v
"""

import unittest

from syllabus_generator import (
    SyllabusData,
    _escape,
    _render_plan_body,
    render_lab_latex,
    render_theory_latex,
)
from pdf_extractor import (
    _fields_from_text,
    _looks_like_lab,
    _parse_units,
    _parse_weekly_plan,
    _strip_signature_footer,
)


class EscapeTests(unittest.TestCase):
    def test_special_characters(self):
        out = _escape(r"A & B_C % $ # { } ~ ^ \ ")
        self.assertIn(r"\&", out)
        self.assertIn(r"\_", out)
        self.assertIn(r"\%", out)
        self.assertIn(r"\$", out)
        self.assertIn(r"\#", out)
        self.assertIn(r"\{", out)
        self.assertIn(r"\}", out)
        self.assertIn(r"\textasciitilde{}", out)
        self.assertIn(r"\textasciicircum{}", out)
        self.assertIn(r"\textbackslash{}", out)

    def test_title_uppercased_before_escaping(self):
        # Regression: .upper() after _escape produced \TEXTASCIITILDE{} etc.
        data = SyllabusData(title="Fluid~Mechanics & Hydraulics")
        tex = render_theory_latex(data)
        self.assertNotIn(r"\TEXTASCIITILDE", tex)
        self.assertIn(r"FLUID\textasciitilde{}MECHANICS \& HYDRAULICS", tex)

    def test_lab_section_title_uppercased_before_escaping(self):
        data = SyllabusData(lab_section_title="Lab & Field~Work")
        tex = render_lab_latex(data)
        self.assertNotIn(r"\TEXTASCIITILDE", tex)
        self.assertIn(r"LAB \& FIELD\textasciitilde{}WORK", tex)


class PlanBodyTests(unittest.TestCase):
    def test_roman_items_beyond_ten(self):
        # Regression: xi./xii. were emitted as "\\ xi. ..." continuations.
        lines = ["a. Outer item"] + [
            f"{r}. item {r}"
            for r in ("i", "ii", "iii", "iv", "v", "ix", "x", "xi", "xii", "xv", "xix", "xx")
        ]
        out = _render_plan_body(lines)
        for r in ("xi", "xii", "xv", "xix", "xx"):
            self.assertIn(rf"\item item {r}", out)
            self.assertNotIn(rf"\\ {r}.", out)
        # Environments must balance.
        self.assertEqual(out.count(r"\begin{enumerate}"), out.count(r"\end{enumerate}"))

    def test_plain_prose_unaffected(self):
        out = _render_plan_body(["Plain prose line."])
        self.assertEqual(out, r"\noindent Plain prose line.\par")


class SplitUnitsTests(unittest.TestCase):
    def test_blank_line_with_stray_space_still_splits(self):
        # Regression: "\n \n" separators merged two units into one.
        from app import split_units

        value = "UNIT I: Alpha\nbody one\n \nUNIT II: Beta\nbody two"
        units = split_units(value)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0], ("UNIT I: Alpha", "body one"))
        self.assertEqual(units[1], ("UNIT II: Beta", "body two"))

    def test_round_trip(self):
        from app import block_from_units, split_units

        units = [("UNIT I: A", "line1\nline2"), ("UNIT II: B", "line3")]
        self.assertEqual(split_units(block_from_units(units)), units)


class FooterStripTests(unittest.TestCase):
    def test_signature_row_removed(self):
        text = "CO6. Evaluate problems.\nBOS Chairperson Dean (Academic) Principal\nCOURSE CONTENT"
        self.assertEqual(
            _strip_signature_footer(text), "CO6. Evaluate problems.\nCOURSE CONTENT"
        )

    def test_content_mentioning_principal_kept(self):
        text = "Principal Component Analysis\nPrincipal"
        # A real content line is kept; a bare footer label line is dropped.
        self.assertEqual(_strip_signature_footer(text), "Principal Component Analysis")

    def test_footer_inside_units_does_not_bleed(self):
        text = _strip_signature_footer(
            "COURSE CONTENT\n"
            "Unit I: Intro\n"
            "line one\n"
            "BOS Chairperson Dean (Academic) Principal\n"
            "Unit II: Next\n"
            "body two\n"
            "Text Books:\n"
            "1. Some Book"
        )
        units = _parse_units(text)
        self.assertEqual(units[0], ("UNIT I: Intro", "line one"))
        self.assertEqual(units[1], ("UNIT II: Next", "body two"))


class WeeklyPlanTests(unittest.TestCase):
    def test_phases_parsed(self):
        text = (
            "Weekly Work Plan\n"
            "I. Phase I: Literature Review\n"
            "a. Survey papers.\n"
            "i. Identify gaps.\n"
            "II. Phase II: Implementation\n"
            "a. Build prototype.\n"
        )
        units = _parse_weekly_plan(text)
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0][0], "Phase I: Literature Review")
        self.assertIn("a. Survey papers.", units[0][1])


LAB_PDF_TEXT = """Estimating and Costing Lab
Total Credits : 01 Subject Code : PCCCE7P001
Teaching Scheme Examination Scheme
Teaching Hrs/Week : – Duration of End Semester Exam : –
Tutorials Hrs/Week : 00 Internal Evaluation : 25 Marks
Practical Hrs/Week : 02 End Semester Examination : 25 Marks
Course Objectives
1 Understand and apply various methods of preliminary and detailed estimation for
Buildings and civil structures including load-bearing and framed types.
2 To impart knowledge on tendering procedures by exposing students to various types
of Tender documents used in civil engineering projects.
Course Outcomes
After completion of syllabus, students would be able to
CO1 Prepare preliminary and detailed estimates for various types of structures using
Standard methods.
CO2 Compute reinforcement quantities using Bar Bending Schedule for RCC members.
CO3 Draft detailed specifications and analyze unit rates of major construction items
using Market data.
CO4 Interpret and compare various types of tender documents used in construction
practices.
CO5 Evaluate property value using depreciation methods and fix standard rent as
per Valuation principles.
ESTIMATING AND COSTING LAB
Minimum 8 practical assignments based on
1. Preliminary estimate using Plinth area method.
2. Detailed estimate of Load bearing structure.
3. Detailed estimate of Frame structure.
4. Calculation of steel with bar bending Schedule.
5. Detailed estimate of earthwork of road for approximate 1km length.
6. Draft detailed specification for 8 major items.
7. Collection of four different types of Tender
8. Calculation of annual and total Depreciation and book value of the end of each
year.
9. Fixation of standard rent of property.
10. Analysis the unit rate of 8 major items of work contained.
11. Market survey for material and labour rates for various items.
* Above is the recommended list but not limited to this"""


class LabExtractionTests(unittest.TestCase):
    def test_lab_pdf_fills_lab_fields(self):
        fields = _fields_from_text(LAB_PDF_TEXT)
        self.assertEqual(fields["lab_table_title"], "Estimating and Costing Lab")
        self.assertEqual(fields["lab_section_title"], "Estimating and Costing Lab")
        self.assertEqual(fields["lab_subject_code"], "PCCCE7P001")
        self.assertEqual(fields["lab_total_credits"], "01")
        self.assertEqual(fields["lab_practical_hours"], "02")
        self.assertEqual(fields["lab_internal_evaluation"], "25 Marks")
        self.assertEqual(fields["lab_end_semester_evaluation"], "25 Marks")
        self.assertEqual(len(fields["lab_objectives"]), 2)
        self.assertEqual(len(fields["lab_outcomes"]), 5)
        self.assertTrue(fields["lab_outcomes"][0].startswith("Prepare preliminary"))

    def test_lab_pdf_experiments(self):
        fields = _fields_from_text(LAB_PDF_TEXT)
        self.assertEqual(
            fields["experiments_heading"], "Minimum 8 practical assignments based on"
        )
        self.assertEqual(len(fields["experiments"]), 11)
        # Wrapped item 8 is joined back into one line.
        self.assertTrue(fields["experiments"][7].endswith("each year."))

    def test_lab_pdf_sets_no_theory_fields(self):
        fields = _fields_from_text(LAB_PDF_TEXT)
        for key in ("title", "units", "textbooks", "reference_books", "objectives", "outcomes"):
            self.assertNotIn(key, fields)

    def test_theory_pdf_not_misdetected(self):
        # Unit-based syllabi are never treated as lab pages.
        self.assertFalse(_looks_like_lab("any text", "Fluid Mechanics", has_units=True))
        self.assertFalse(_looks_like_lab("plain theory text", "Fluid Mechanics", has_units=False))


class RenderSmokeTests(unittest.TestCase):
    def test_theory_defaults_render(self):
        tex = render_theory_latex(SyllabusData())
        self.assertIn(r"\begin{document}", tex)
        self.assertIn("FLUID MECHANICS", tex)
        self.assertIn("Text books:", tex)
        self.assertEqual(tex.count(r"\begin{enumerate}"), tex.count(r"\end{enumerate}"))

    def test_lab_defaults_render(self):
        tex = render_lab_latex(SyllabusData())
        self.assertIn("FLUID MECHANICS LAB", tex)
        self.assertIn("Perform any 08 Experiments.", tex)

    def test_empty_experiments_omits_enumerate(self):
        # Regression: empty Experiments box produced an itemless enumerate,
        # a LaTeX error ("Something's wrong--perhaps a missing \item").
        data = SyllabusData(experiments=[])
        tex = render_lab_latex(data)
        self.assertNotIn(r"\begin{enumerate}[label=\arabic*.]", tex)
        self.assertEqual(tex.count(r"\begin{enumerate}"), tex.count(r"\end{enumerate}"))

    def test_signature_box_toggle(self):
        on = render_theory_latex(SyllabusData(include_theory_signature_box=True))
        off = render_theory_latex(SyllabusData(include_theory_signature_box=False))
        self.assertIn(r"\fancyfoot", on)
        self.assertNotIn(r"\fancyfoot", off)


if __name__ == "__main__":
    unittest.main(verbosity=2)
