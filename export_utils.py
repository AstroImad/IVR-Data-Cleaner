"""Utilities for creating safe Excel downloads."""

import io
import re
from typing import Optional

import pandas as pd


_INVALID_SHEET_CHARACTERS = re.compile(r"[\\/*?:\[\]]")


def normalize_column_labels(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy whose column labels are readable, unique strings."""
    safe = df.copy()
    column_names = []
    used_names = set()
    for column in safe.columns:
        base_name = str(column)
        name = base_name
        suffix = 1
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)
        column_names.append(name)
    safe.columns = column_names
    return safe


def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with Excel-safe column labels and formula text."""
    safe = normalize_column_labels(df)

    for column in safe.select_dtypes(include=["object", "string"]).columns:
        safe[column] = safe[column].map(
            lambda value: f"'{value}"
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
            else value
        )
    return safe


def _safe_sheet_name(name: str, used_names: set[str]) -> str:
    """Return a valid, unique Excel worksheet name."""
    base = _INVALID_SHEET_CHARACTERS.sub("_", str(name or "Sheet"))[:31] or "Sheet"
    candidate = base
    suffix = 1
    while candidate in used_names:
        suffix_text = f"_{suffix}"
        candidate = f"{base[:31 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def to_excel(
    completed_df: pd.DataFrame,
    partial_df: Optional[pd.DataFrame] = None,
    no_response_df: Optional[pd.DataFrame] = None,
    skipped_df: Optional[pd.DataFrame] = None,
    skipped_label: str = "Skipped",
    validation_df: Optional[pd.DataFrame] = None,
) -> bytes:
    """Create a workbook separated by response status."""
    output = io.BytesIO()
    used_sheet_names: set[str] = set()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        _excel_safe(completed_df).to_excel(
            writer,
            index=False,
            sheet_name=_safe_sheet_name("Completed Responses", used_sheet_names),
        )
        for frame, sheet_name in (
            (partial_df, "Partial Responses"),
            (no_response_df, "No IVR Response"),
            (skipped_df, skipped_label),
            (validation_df, "Data Quality Issues"),
        ):
            if frame is not None and not frame.empty:
                _excel_safe(frame).to_excel(
                    writer,
                    index=False,
                    sheet_name=_safe_sheet_name(sheet_name, used_sheet_names),
                )
    return output.getvalue()