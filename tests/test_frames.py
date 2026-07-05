"""Tests for the DataFrame pipeline and pass-row coloring used by the UI."""

from __future__ import annotations

import pandas as pd
import pytest

from vetting.frames import (
    MULTI_STYLE,
    PLATFORM_STYLES,
    MissingColumnsError,
    resolve_columns,
    row_style,
    run_over_dataframe,
    write_colored_xlsx,
)
from vetting.models import PlatformResult, RowResult, Verdict


def test_resolve_columns_by_header_ignoring_extras_and_order():
    df = pd.DataFrame(columns=[
        "Email", "TikTok", "YouTube", "Your country/region",
        "Instagram", "Linkedin", "Random Extra",
    ])
    colmap = resolve_columns(df)
    assert colmap == {
        "youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok",
        "linkedin": "Linkedin", "country": "Your country/region",
    }


def test_resolve_columns_missing_raises():
    df = pd.DataFrame(columns=["YouTube", "Instagram", "TikTok"])  # no linkedin/country
    with pytest.raises(MissingColumnsError) as exc:
        resolve_columns(df)
    assert "linkedin" in str(exc.value) and "country" in str(exc.value)


def _result(index: int = 0, **verdicts) -> RowResult:
    res = RowResult(row_index=index)
    for platform, verdict in verdicts.items():
        setattr(res, platform, PlatformResult(verdict=verdict))
    return res


def test_row_style_single_platform_colors():
    assert row_style(_result(instagram=Verdict.PASS)) is PLATFORM_STYLES["instagram"]
    assert row_style(_result(youtube=Verdict.PASS)) is PLATFORM_STYLES["youtube"]
    assert row_style(_result(linkedin=Verdict.PASS)) is PLATFORM_STYLES["linkedin"]
    assert row_style(_result(tiktok=Verdict.PASS)) is PLATFORM_STYLES["tiktok"]


def test_row_style_multi_is_yellow():
    style = row_style(_result(youtube=Verdict.PASS, instagram=Verdict.PASS))
    assert style is MULTI_STYLE


def test_row_style_none_when_no_pass():
    assert row_style(_result(instagram=Verdict.FAIL, youtube=Verdict.REVIEW)) is None
    assert row_style(_result()) is None


def test_run_over_dataframe_free_mode_and_exclusion():
    df = pd.DataFrame({
        "YouTube": ["chan", None],
        "Instagram": ["someone", "other"],
        "TikTok": [None, None],
        "Linkedin": [None, None],
        "Your country/region": ["Spain", "India"],  # 2nd row excluded
    })
    colmap = resolve_columns(df)
    results = run_over_dataframe(df, colmap)  # no clients -> nothing passes
    assert len(results) == 2
    assert results[1].overall is Verdict.EXCLUDED
    # Nothing passes in free mode, so no row is highlighted.
    assert all(row_style(r) is None for r in results)


def test_write_colored_xlsx_fills_only_passing_rows():
    df = pd.DataFrame({
        "YouTube": ["a", "b"],
        "Instagram": ["x", "y"],
        "TikTok": [None, None],
        "Linkedin": [None, None],
        "Your country/region": ["Spain", "Spain"],
    })
    from io import BytesIO
    buf = BytesIO()
    df.to_excel(buf, index=False)

    results = [_result(0, instagram=Verdict.PASS), _result(1, instagram=Verdict.FAIL)]
    out = write_colored_xlsx(buf.getvalue(), results)

    from openpyxl import load_workbook
    ws = load_workbook(BytesIO(out)).active
    # Row 2 (first data row) passed -> purple fill; row 3 untouched.
    assert ws.cell(2, 1).fill.fgColor.rgb.endswith("800080")
    assert ws.cell(3, 1).fill.fill_type in (None, "none")


def test_partial_run_preserves_original_positions():
    df = pd.DataFrame({
        "YouTube": [None, None, None],
        "Instagram": ["a", "b", "c"],
        "TikTok": [None, None, None],
        "Linkedin": [None, None, None],
        "Your country/region": ["Spain", "Spain", "Spain"],
    })
    colmap = resolve_columns(df)
    # Vet only rows at positions 1 and 2 (a partial batch).
    results = run_over_dataframe(df.iloc[1:3], colmap)
    assert [r.row_index for r in results] == [1, 2]
