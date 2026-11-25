import unittest

from text_cleanup import clean_field_text, normalize_unit_heading, tidy_line, tidy_list, tidy_units


class TidyLineTests(unittest.TestCase):
    def test_spaces(self):
        self.assertEqual(tidy_line("  Fluid    mechanics\t basics  "), "Fluid mechanics basics")

    def test_space_before_punctuation(self):
        self.assertEqual(tidy_line("Stress , strain ; and ( elastic ) limit ."),
                         "Stress, strain; and (elastic) limit.")

    def test_space_after_punctuation(self):
        self.assertEqual(tidy_line("Stress,strain;modulus"), "Stress, strain; modulus")
        self.assertEqual(tidy_line("Flow ends here.Next topic"), "Flow ends here. Next topic")

    def test_numbers_untouched(self):
        self.assertEqual(tidy_line("Load 1,000 kN at 3.5 m, 10:30 hrs"), "Load 1,000 kN at 3.5 m, 10:30 hrs")

    def test_initials_untouched(self):
        self.assertEqual(tidy_line("Thomas H. Cormen, Introduction to Algorithms, PHI Pub."),
                         "Thomas H. Cormen, Introduction to Algorithms, PHI Pub.")

    def test_pdf_ligatures_and_hidden_chars(self):
        self.assertEqual(tidy_line("deﬁne the ﬂow rate​"), "define the flow rate")

    def test_double_punctuation(self):
        self.assertEqual(tidy_line("Stress,, strain"), "Stress, strain")


class TidyListTests(unittest.TestCase):
    def test_numbering_and_bullets_removed(self):
        text = "1. understand stress\n2) Apply theory,\n• Draw SFD\nCO4: Design beams\n(a) Analyse trusses"
        self.assertEqual(tidy_list(text),
                         "Understand stress\nApply theory\nDraw SFD\nDesign beams\nAnalyse trusses")

    def test_blank_lines_removed(self):
        self.assertEqual(tidy_list("One\n\n\nTwo\n"), "One\nTwo")

    def test_wrapped_lines_joined_only_when_asked(self):
        text = "To understand the basic\nconcepts of fluid flow\nTo apply Bernoulli"
        self.assertEqual(tidy_list(text, join_wrapped=True),
                         "To understand the basic concepts of fluid flow\nTo apply Bernoulli")
        self.assertEqual(tidy_list(text), "To understand the basic\nConcepts of fluid flow\nTo apply Bernoulli")

    def test_books_keep_lowercase_start(self):
        self.assertEqual(tidy_list("1. de Silva, Fluid Mechanics, Pearson", is_book=True),
                         "de Silva, Fluid Mechanics, Pearson")

    def test_values_with_numbers_kept(self):
        self.assertEqual(tidy_list("3.5 kN load test"), "3.5 kN load test")


class UnitTests(unittest.TestCase):
    def test_heading_formats(self):
        self.assertEqual(normalize_unit_heading("Unit 3 - fluid dynamics"), "UNIT III: Fluid dynamics")
        self.assertEqual(normalize_unit_heading("UNIT-IV : Boundary Layer"), "UNIT IV: Boundary Layer")
        self.assertEqual(normalize_unit_heading("unit ii:Kinematics"), "UNIT II: Kinematics")
        self.assertEqual(normalize_unit_heading("Unit V"), "UNIT V:")
        self.assertEqual(normalize_unit_heading("Phase I: Literature survey"), "Phase I: Literature survey")
        self.assertEqual(normalize_unit_heading("United approach"), "United approach")

    def test_missing_blank_line_between_units(self):
        text = "Unit 1: Intro\nBody one\nUnit 2: Flow\nBody two"
        self.assertEqual(tidy_units(text), "UNIT I: Intro\nBody one\n\nUNIT II: Flow\nBody two")

    def test_extra_blank_lines_and_spaces(self):
        text = "UNIT I:  Intro \n  Body   one \n\n\n\n UNIT II: Flow\nBody two"
        self.assertEqual(tidy_units(text), "UNIT I: Intro\nBody one\n\nUNIT II: Flow\nBody two")

    def test_body_capitalized_but_markers_kept(self):
        text = "UNIT I: Intro\nproperties of fluids\ni. viscosity\na) surface tension"
        self.assertEqual(tidy_units(text),
                         "UNIT I: Intro\nProperties of fluids\ni. viscosity\na) surface tension")

    def test_wrapped_body_joined(self):
        text = "UNIT I: Intro\nProperties of fluids, viscosity,\nsurface tension"
        self.assertEqual(tidy_units(text, join_wrapped=True),
                         "UNIT I: Intro\nProperties of fluids, viscosity, surface tension")


class DispatchTests(unittest.TestCase):
    def test_single_line_field(self):
        self.assertEqual(clean_field_text("title", "  Fluid   Mechanics "), "Fluid Mechanics")

    def test_list_field(self):
        self.assertEqual(clean_field_text("outcomes", "CO1 : explain flow"), "Explain flow")

    def test_idempotent(self):
        text = "1. understand ,stress\nUnit 2 - flow"
        once = clean_field_text("objectives", text, join_wrapped=True)
        self.assertEqual(clean_field_text("objectives", once, join_wrapped=True), once)


if __name__ == "__main__":
    unittest.main()
