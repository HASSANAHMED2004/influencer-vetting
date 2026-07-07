"""Tests for the DataFrame pipeline and pass-row coloring used by the UI."""

from __future__ import annotations

import pandas as pd
import pytest

from vetting.frames import (
    MULTI_STYLE,
    PLATFORM_STYLES,
    MissingColumnsError,
    passing_frame,
    resolve_columns,
    row_style,
    run_over_dataframe,
    write_passing_xlsx,
)
from vetting.models import PlatformResult, RowResult, Verdict


def test_resolve_columns_by_header_ignoring_extras_and_order():
    df = pd.DataFrame(columns=[
        "Email", "TikTok", "YouTube", "LinkedIn", "Your country/region",
        "Instagram", "Random Extra",
    ])
    colmap = resolve_columns(df)
    assert colmap == {
        "youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok",
        "linkedin": "LinkedIn", "country": "Your country/region",
    }


def test_resolve_columns_missing_raises():
    df = pd.DataFrame(columns=["YouTube", "Instagram"])  # no tiktok/country
    with pytest.raises(MissingColumnsError) as exc:
        resolve_columns(df)
    assert "tiktok" in str(exc.value) and "country" in str(exc.value)


def _result(index: int = 0, **verdicts) -> RowResult:
    res = RowResult(row_index=index)
    for platform, verdict in verdicts.items():
        setattr(res, platform, PlatformResult(verdict=verdict))
    return res


def test_row_style_single_platform_colors():
    assert row_style(_result(instagram=Verdict.PASS)) is PLATFORM_STYLES["instagram"]
    assert row_style(_result(youtube=Verdict.PASS)) is PLATFORM_STYLES["youtube"]
    assert row_style(_result(tiktok=Verdict.PASS)) is PLATFORM_STYLES["tiktok"]
    assert row_style(_result(linkedin=Verdict.PASS)) is PLATFORM_STYLES["linkedin"]


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
        "LinkedIn": [None, None],
        "Your country/region": ["Spain", "India"],  # 2nd row excluded
    })
    colmap = resolve_columns(df)
    results = run_over_dataframe(df, colmap)  # no clients -> nothing passes
    assert len(results) == 2
    assert results[1].overall is Verdict.EXCLUDED
    # Nothing passes in free mode, so no row is highlighted.
    assert all(row_style(r) is None for r in results)


def test_partial_run_preserves_original_positions():
    df = pd.DataFrame({
        "YouTube": [None, None, None],
        "Instagram": ["a", "b", "c"],
        "TikTok": [None, None, None],
        "LinkedIn": [None, None, None],
        "Your country/region": ["Spain", "Spain", "Spain"],
    })
    colmap = resolve_columns(df)
    # Vet only rows at positions 1 and 2 (a partial batch).
    results = run_over_dataframe(df.iloc[1:3], colmap)
    assert [r.row_index for r in results] == [1, 2]


def test_passing_frame_keeps_only_passing_rows_in_order():
    df = pd.DataFrame({"Name": ["Alice", "Bob", "Cara", "Dan"]})
    results = [
        _result(0, instagram=Verdict.PASS),                    # kept (purple)
        _result(1, instagram=Verdict.FAIL),                    # dropped
        _result(2, instagram=Verdict.PASS, youtube=Verdict.PASS),  # kept (yellow)
        _result(3, youtube=Verdict.PASS),                      # kept (red)
    ]
    styler = passing_frame(df, results)
    assert list(styler.data.index) == [0, 2, 3]
    assert list(styler.data["Name"]) == ["Alice", "Cara", "Dan"]
    # Leading "Row" column = original spreadsheet row (0-based pos + 2).
    assert list(styler.data["Row"]) == [2, 4, 5]
    assert list(styler.data.columns)[0] == "Row"


def test_passing_frame_empty_when_nothing_passes():
    df = pd.DataFrame({"Name": ["Alice", "Bob"]})
    results = [_result(0, instagram=Verdict.FAIL), _result(1, youtube=Verdict.FAIL)]
    assert len(passing_frame(df, results).data) == 0


def test_write_passing_xlsx_only_passing_rows_with_values_and_color():
    from io import BytesIO

    from openpyxl import load_workbook

    df = pd.DataFrame({
        "YouTube": ["a", "b", "c"],
        "Instagram": ["x", "y", "z"],
        "TikTok": [None, None, None],
        "LinkedIn": [None, None, None],
        "Your country/region": ["Spain", "Spain", "Spain"],
    })
    buf = BytesIO()
    df.to_excel(buf, index=False)

    results = [
        _result(0, instagram=Verdict.PASS),
        _result(1, instagram=Verdict.FAIL),  # dropped
        _result(2, youtube=Verdict.PASS),
    ]
    ws = load_workbook(BytesIO(write_passing_xlsx(buf.getvalue(), results))).active
    # Header + exactly the 2 passing rows, in order.
    assert ws.max_row == 3
    # Col 1 is the original spreadsheet row number; col 2 is the first real column.
    assert [ws.cell(r, 1).value for r in (1, 2, 3)] == ["Row", 2, 4]
    assert [ws.cell(r, 2).value for r in (1, 2, 3)] == ["YouTube", "a", "c"]
    assert ws.cell(2, 1).fill.fgColor.rgb.endswith("800080")  # IG purple (Row cell too)
    assert ws.cell(3, 2).fill.fgColor.rgb.endswith("FF0000")  # YouTube red
