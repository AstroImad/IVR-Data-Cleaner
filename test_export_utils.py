"""Regression tests for final-step Excel export."""

import io
import unittest

import pandas as pd

from cleaning import apply_column_renames

from export_utils import normalize_column_labels, to_excel


class ExcelExportTests(unittest.TestCase):
    def test_normalize_column_labels_converts_mixed_labels_to_unique_strings(self):
        frame = pd.DataFrame([[1, 2, 3]], columns=["0", 0, 1])

        normalized = normalize_column_labels(frame)

        self.assertEqual(normalized.columns.tolist(), ["0", "0_1", "1"])

    def test_export_handles_mixed_column_labels_and_formula_text(self):
        frame = pd.DataFrame(
            {
                "phonenum": ["100"],
                0: ["=SUM(A1:A2)"],
                1: ["answer"],
            }
        )

        workbook_bytes = to_excel(
            completed_df=frame,
            skipped_df=frame.copy(),
            skipped_label="Skipped / Redirected: survey",
        )

        self.assertGreater(len(workbook_bytes), 0)
        workbook = pd.ExcelFile(io.BytesIO(workbook_bytes), engine="openpyxl")
        self.assertEqual(
            workbook.sheet_names,
            ["Completed Responses", "Skipped _ Redirected_ survey"],
        )
        exported = pd.read_excel(io.BytesIO(workbook_bytes), sheet_name="Completed Responses")
        self.assertEqual(exported.columns.tolist(), ["phonenum", "0", "1"])
        self.assertEqual(exported.loc[0, "0"], "'=SUM(A1:A2)")

    def test_export_handles_data_without_phonenum(self):
        workbook_bytes = to_excel(completed_df=pd.DataFrame({0: ["answer"]}))

        workbook = pd.ExcelFile(io.BytesIO(workbook_bytes), engine="openpyxl")
        self.assertEqual(workbook.sheet_names, ["Completed Responses"])
        self.assertEqual(
            pd.read_excel(io.BytesIO(workbook_bytes)).columns.tolist(),
            ["0"],
        )

    def test_column_mapping_handles_data_without_phonenum(self):
        frame = pd.DataFrame({0: ["FlowNo_1=1"]})
        mapped, _ = apply_column_renames(frame, {1: "Question"}, {1: [0]})
        self.assertIn("phonenum", mapped.columns)
        self.assertTrue(mapped["phonenum"].isna().all())


if __name__ == "__main__":
    unittest.main(verbosity=2)