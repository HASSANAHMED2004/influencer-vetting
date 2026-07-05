"""DataFrame-driven pipeline for the Streamlit UI.

Resolves the required columns by header name (so extra columns don't matter and
positions can shift), runs the per-row vetting, and turns results into row colors.
The rule: only *passing* rows are highlighted, whole-row, by which platform passed.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from io import BytesIO

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill

from .models import ExclusionRule, RowResult, Verdict
from .pipeline import process_row

# Required logical columns -> keywords matched against the uploaded headers
# (case-insensitive: exact match preferred, else substring).
REQUIRED_COLUMNS: dict[str, str] = {
    "youtube": "youtube",
    "instagram": "instagram",
    "tiktok": "tiktok",
    "linkedin": "linkedin",
    "country": "country",
}


@dataclass(frozen=True)
class RowStyle:
    """A whole-row highlight: a label plus fill and font colors (#RRGGBB)."""

    label: str
    fill: str
    font: str


# Per-platform colors (single platform passed) and the multi-platform color.
PLATFORM_STYLES: dict[str, RowStyle] = {
    "instagram": RowStyle("Instagram", "#800080", "#FFFFFF"),  # purple
    "youtube": RowStyle("YouTube", "#FF0000", "#FFFFFF"),  # red
    "linkedin": RowStyle("LinkedIn", "#0000FF", "#FFFFFF"),  # blue
    "tiktok": RowStyle("TikTok", "#000000", "#FFFFFF"),  # black
}
MULTI_STYLE = RowStyle("Multiple platforms", "#FFFF00", "#000000")  # yellow


class MissingColumnsError(ValueError):
    """Raised when the uploaded dataset lacks one of the required columns."""


def resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """Map each logical column to the actual header in ``df``.

    Extra columns are ignored; only the required ones must be present. Raises
    MissingColumnsError listing any that couldn't be found.
    """
    by_lower = {str(c).strip().lower(): c for c in df.columns}
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for logical, keyword in REQUIRED_COLUMNS.items():
        if keyword in by_lower:
            resolved[logical] = by_lower[keyword]
            continue
        match = next((actual for low, actual in by_lower.items() if keyword in low), None)
        if match is None:
            missing.append(logical)
        else:
            resolved[logical] = match
    if missing:
        raise MissingColumnsError(
            f"Uploaded file is missing required column(s): {missing}. "
            f"Found: {list(df.columns)}"
        )
    return resolved


@dataclass
class _DFRow:
    """Adapter exposing the attributes ``process_row`` expects from a df row."""

    row_index: int
    youtube: object
    instagram: object
    tiktok: object
    linkedin: object
    country: object


def _cell(value: object) -> object:
    """Normalize pandas empties (NaN/NaT) to None so downstream logic is clean."""
    return None if pd.isna(value) else value


def run_over_dataframe(
    df: pd.DataFrame,
    colmap: dict[str, str],
    *,
    youtube_client=None,
    social_client=None,
    exclusion_rules: Sequence[ExclusionRule] | None = None,
    disabled_platforms: Collection[str] = (),
    progress: Callable[[float], None] | None = None,
) -> list[RowResult]:
    """Run the vetting pipeline over every row, in order. Returns aligned results."""
    results: list[RowResult] = []
    total = max(len(df), 1)
    for done, (position, row) in enumerate(df.iterrows(), start=1):
        adapter = _DFRow(
            row_index=int(position),  # original position in the full dataset
            youtube=_cell(row[colmap["youtube"]]),
            instagram=_cell(row[colmap["instagram"]]),
            tiktok=_cell(row[colmap["tiktok"]]),
            linkedin=_cell(row[colmap["linkedin"]]),
            country=_cell(row[colmap["country"]]),
        )
        results.append(process_row(
            adapter, youtube_client=youtube_client,
            social_client=social_client, exclusion_rules=exclusion_rules,
            disabled_platforms=disabled_platforms,
        ))
        if progress is not None:
            progress(done / total)
    return results


def passed_platforms(result: RowResult) -> list[str]:
    """Platforms whose verdict is PASS for this row."""
    pairs = (
        ("youtube", result.youtube),
        ("instagram", result.instagram),
        ("tiktok", result.tiktok),
        ("linkedin", result.linkedin),
    )
    return [name for name, pr in pairs if pr.verdict is Verdict.PASS]


def row_style(result: RowResult) -> RowStyle | None:
    """The highlight for a row: platform color if exactly one passed, yellow if
    more than one, and None (no highlight) if none passed."""
    passed = passed_platforms(result)
    if not passed:
        return None
    if len(passed) >= 2:
        return MULTI_STYLE
    return PLATFORM_STYLES[passed[0]]


def style_dataframe(df: pd.DataFrame, results: list[RowResult]) -> pd.io.formats.style.Styler:
    """Return a pandas Styler that fills each passing row with its color.

    Rows are matched by their original position (``result.row_index``), so a
    partial run only colors the rows that were actually vetted.
    """
    fills = {res.row_index: s for res in results if (s := row_style(res)) is not None}

    def _apply(row: pd.Series) -> list[str]:
        style = fills.get(row.name)
        if style is None:
            return [""] * len(row)
        return [f"background-color: {style.fill}; color: {style.font};"] * len(row)

    return df.style.apply(_apply, axis=1)


def write_colored_xlsx(uploaded_bytes: bytes, results: list[RowResult]) -> bytes:
    """Return the uploaded workbook with passing rows whole-row color-filled.

    Assumes one header row; data row i (0-based) maps to sheet row i+2. Only the
    first sheet is annotated. Non-passing rows are left untouched.
    """
    workbook = load_workbook(BytesIO(uploaded_bytes))
    worksheet = workbook[workbook.sheetnames[0]]
    for result in results:
        style = row_style(result)
        if style is None:
            continue
        fill = PatternFill("solid", fgColor=style.fill.lstrip("#"))
        font = Font(color=style.font.lstrip("#"))
        sheet_row = result.row_index + 2  # +1 header, +1 for 1-based rows
        for col in range(1, worksheet.max_column + 1):
            cell = worksheet.cell(sheet_row, col)
            cell.fill = fill
            cell.font = font
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def color_summary(results: list[RowResult]) -> dict[str, int]:
    """Count rows per highlight label (for the UI summary)."""
    counts: dict[str, int] = {}
    for result in results:
        style = row_style(result)
        if style is None:
            continue
        label = MULTI_STYLE.label if style is MULTI_STYLE else style.label
        counts[label] = counts.get(label, 0) + 1
    return counts
