import json
import unittest

from draft_io import (
    DRAFT_FORMAT,
    DraftError,
    draft_file_name,
    draft_from_json,
    draft_to_json,
    subject_file_stem,
)
from syllabus_generator import SyllabusData


class DraftRoundTripTests(unittest.TestCase):
    def test_round_trip_keeps_every_field(self):
        data = SyllabusData()
        data.title = "Strength of Materials ₹ & % test"
        data.units = [("UNIT I: Stress", "Body line 1\nBody line 2"), ("UNIT II: Strain", "")]
        data.objectives = ["Obj one", "Obj two"]
        data.include_theory_signature_box = False

        restored = SyllabusData()
        for name, value in draft_from_json(draft_to_json(data).encode("utf-8")).items():
            setattr(restored, name, value)
        self.assertEqual(restored, data)

    def test_units_come_back_as_tuples(self):
        loaded = draft_from_json(draft_to_json(SyllabusData()))
        self.assertTrue(all(isinstance(u, tuple) for u in loaded["units"]))

    def test_unknown_fields_ignored_missing_fields_untouched(self):
        payload = {"format": DRAFT_FORMAT, "version": 1,
                   "fields": {"title": "X", "not_a_field": 5}}
        self.assertEqual(draft_from_json(json.dumps(payload)), {"title": "X"})

    def test_utf8_bom_accepted(self):
        raw = "﻿" + draft_to_json(SyllabusData())
        self.assertIn("title", draft_from_json(raw.encode("utf-8")))


class DraftRejectionTests(unittest.TestCase):
    def assertRejected(self, raw):
        with self.assertRaises(DraftError):
            draft_from_json(raw)

    def test_not_json(self):
        self.assertRejected(b"hello")

    def test_other_json(self):
        self.assertRejected(json.dumps({"a": 1}))

    def test_newer_version(self):
        self.assertRejected(json.dumps({"format": DRAFT_FORMAT, "version": 99, "fields": {"title": "X"}}))

    def test_wrong_type(self):
        self.assertRejected(json.dumps({"format": DRAFT_FORMAT, "version": 1, "fields": {"objectives": "one"}}))
        self.assertRejected(json.dumps({"format": DRAFT_FORMAT, "version": 1, "fields": {"units": [["only heading"]]}}))
        self.assertRejected(json.dumps({"format": DRAFT_FORMAT, "version": 1, "fields": {"include_lab_signature_box": "yes"}}))

    def test_too_large(self):
        self.assertRejected(b" " * 2_000_000)


class DraftFileNameTests(unittest.TestCase):
    def test_name_from_code_and_title(self):
        data = SyllabusData()
        data.subject_code, data.title = "BTCE 301T", "Fluid Mechanics / I"
        self.assertEqual(draft_file_name(data), "draft_BTCE_301T_Fluid_Mechanics_I.json")

    def test_empty_name(self):
        data = SyllabusData()
        data.subject_code, data.title = "", "  "
        self.assertEqual(draft_file_name(data), "draft_syllabus.json")


class SubjectFileStemTests(unittest.TestCase):
    def test_code_and_name(self):
        self.assertEqual(subject_file_stem("PCCCE5T001", "Fluid Mechanics", "x"), "PCCCE5T001_Fluid_Mechanics")

    def test_unsafe_characters_replaced(self):
        self.assertEqual(subject_file_stem("CE-301", "Design of R.C.C. / Steel", "x"), "CE_301_Design_of_R_C_C_Steel")

    def test_only_name(self):
        self.assertEqual(subject_file_stem("", "Fluid Mechanics Lab", "x"), "Fluid_Mechanics_Lab")

    def test_fallback(self):
        self.assertEqual(subject_file_stem(" ", "", "lab_syllabus"), "lab_syllabus")


if __name__ == "__main__":
    unittest.main()
