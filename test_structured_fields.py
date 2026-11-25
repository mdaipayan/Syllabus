import unittest

from structured_fields import (
    book_needs_check,
    book_parse_is_exact,
    compose_heading,
    detect_heading_style,
    format_book,
    format_number,
    panel_title,
    parse_book,
    parse_number,
    split_heading,
    to_roman,
)
from syllabus_generator import SyllabusData


class UnitHeadingTests(unittest.TestCase):
    def test_roman(self):
        self.assertEqual([to_roman(n) for n in (1, 4, 9, 12, 14, 19, 40)],
                         ["I", "IV", "IX", "XII", "XIV", "XIX", "XL"])

    def test_split(self):
        self.assertEqual(split_heading("UNIT III: Fluid Dynamics"), ("UNIT", "Fluid Dynamics"))
        self.assertEqual(split_heading("Phase II: Design"), ("Phase", "Design"))
        self.assertEqual(split_heading("Introduction"), ("Custom", "Introduction"))

    def test_compose(self):
        self.assertEqual(compose_heading("UNIT", 3, "Fluid Dynamics"), "UNIT III: Fluid Dynamics")
        self.assertEqual(compose_heading("Phase", 2, ""), "Phase II:")
        self.assertEqual(compose_heading("Custom", 1, "Intro"), "Intro")
        self.assertEqual(compose_heading("Custom", 2, " "), "Part 2")

    def test_round_trip_defaults(self):
        units = SyllabusData().units
        style = detect_heading_style(units)
        self.assertEqual(style, "UNIT")
        rebuilt = [compose_heading(style, i, panel_title(style, h)) for i, (h, _) in enumerate(units, 1)]
        self.assertEqual(rebuilt, [h for h, _ in units])

    def test_detect(self):
        self.assertEqual(detect_heading_style([("Phase I: A", ""), ("Phase II: B", "")]), "Phase")
        self.assertEqual(detect_heading_style([("UNIT I: A", ""), ("Intro", "")]), "Custom")
        self.assertEqual(detect_heading_style([]), "UNIT")


class BookTests(unittest.TestCase):
    SAMPLES = [
        "Thomas H. Cormen, Introduction to Algorithms, PHI Pub.",
        "Parag Himanshu Dave, Balchandra Dave, Design and Analysis of Algorithms, Education, O'Reilly Pub.",
        "R.K.Bansal, A Text Book of Fluid Mechanics and Hydraulic Machines, Laxmi Pub.",
    ]

    def test_samples_round_trip(self):
        for line in self.SAMPLES:
            self.assertTrue(book_parse_is_exact(line), line)

    def test_parse_two_authors(self):
        self.assertEqual(parse_book(self.SAMPLES[1]),
                         ("Parag Himanshu Dave, Balchandra Dave", "Design and Analysis of Algorithms",
                          "Education, O'Reilly"))

    def test_format_adds_pub(self):
        self.assertEqual(format_book("Thomas H. Cormen", "Introduction to Algorithms", "PHI"),
                         "Thomas H. Cormen, Introduction to Algorithms, PHI Pub.")
        self.assertEqual(format_book("A", "B", "Laxmi Publications"), "A, B, Laxmi Publications")
        self.assertEqual(format_book("A", "B", "Oxford University Press"), "A, B, Oxford University Press")
        self.assertEqual(format_book("", "  Only   title ", ""), "Only title")

    def test_short_lines(self):
        self.assertEqual(parse_book("Fluid Mechanics"), ("", "Fluid Mechanics", ""))
        self.assertEqual(parse_book("Cormen, Algorithms"), ("Cormen", "Algorithms", ""))
        self.assertEqual(parse_book("Algorithms, PHI Pub."), ("", "Algorithms", "PHI"))
        self.assertEqual(parse_book(""), ("", "", ""))

    def test_no_words_lost_for_default_books(self):
        data = SyllabusData()
        words = lambda text: text.replace(",", " ").split()
        for line in data.textbooks + data.reference_books:
            self.assertEqual(words(format_book(*parse_book(line))), words(line), line)

    def test_lone_pub_joined_to_publisher(self):
        line = "P.N. Modi & S.M. Set, Hydraulics, Standard Book House, Pub."
        self.assertEqual(parse_book(line), ("P.N. Modi & S.M. Set", "Hydraulics", "Standard Book House"))

    def test_needs_check(self):
        self.assertFalse(book_needs_check(self.SAMPLES[0]))
        self.assertTrue(book_needs_check(self.SAMPLES[1]))  # two authors
        self.assertTrue(book_needs_check("Modi, Hydraulics, Fluid Mechanics, Standard Pub."))
        self.assertFalse(book_needs_check(""))


class NumberTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_number("03 Hours"), 3.0)
        self.assertEqual(parse_number("40 Marks"), 40.0)
        self.assertEqual(parse_number("1.5"), 1.5)
        self.assertIsNone(parse_number("NA"))

    def test_format_matches_defaults(self):
        data = SyllabusData()
        self.assertEqual(format_number("credits", parse_number(data.total_credits)), data.total_credits)
        self.assertEqual(format_number("count", parse_number(data.practical_hours)), data.practical_hours)
        self.assertEqual(format_number("hours", parse_number(data.exam_duration)), data.exam_duration)
        self.assertEqual(format_number("marks", parse_number(data.internal_evaluation)), data.internal_evaluation)

    def test_format_fractions(self):
        self.assertEqual(format_number("credits", 1.5), "1.5")
        self.assertEqual(format_number("hours", 2.5), "2.5 Hours")
        self.assertEqual(format_number("marks", 100), "100 Marks")


if __name__ == "__main__":
    unittest.main()
