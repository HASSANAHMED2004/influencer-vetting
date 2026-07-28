"""Tests for the out-of-credits safeguard.

The behaviour that matters: a drained API key stops the run early instead of
silently marking every remaining row REVIEW, and the rows already vetted (the
passing ones especially) survive the abort.
"""

from __future__ import annotations

import pandas as pd
import pytest
import requests

from vetting.config import QUOTA_ABORT_AFTER
from vetting.frames import passing_frame, resolve_columns, run_over_dataframe
from vetting.models import PlatformResult, RowResult, Verdict
from vetting.quota import QuotaExhausted, QuotaGuard, http_status, is_quota_error


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} error", response=response)


def _row(index: int, **platforms) -> RowResult:
    res = RowResult(row_index=index)
    for platform, outcome in platforms.items():
        setattr(res, platform, outcome)
    return res


def _quota_hit(status: int = 402) -> PlatformResult:
    return PlatformResult(verdict=Verdict.REVIEW, note="api error", error_status=status)


# --- status classification -------------------------------------------------


def test_http_status_extracts_code_and_tolerates_no_response():
    assert http_status(_http_error(429)) == 429
    assert http_status(requests.ConnectionError("no network")) is None


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_plan_failures_are_quota_errors(status):
    assert is_quota_error(_http_error(status))


@pytest.mark.parametrize("status", [404, 500, 503])
def test_profile_and_server_failures_are_not_quota_errors(status):
    # A 404 is "this profile is private/gone" — the key is fine, keep going.
    assert not is_quota_error(_http_error(status))


def test_network_error_is_not_a_quota_error():
    assert not is_quota_error(requests.ConnectionError("no network"))


# --- the guard -------------------------------------------------------------


def test_guard_aborts_after_consecutive_quota_failures():
    guard = QuotaGuard()
    results: list[RowResult] = []
    for i in range(QUOTA_ABORT_AFTER - 1):  # under the threshold: no abort yet
        row = _row(i, instagram=_quota_hit())
        results.append(row)
        guard.observe(row, results)

    final = _row(QUOTA_ABORT_AFTER - 1, instagram=_quota_hit())
    results.append(final)
    with pytest.raises(QuotaExhausted) as exc:
        guard.observe(final, results)

    assert exc.value.platform == "instagram"
    assert exc.value.status == 402
    assert exc.value.rows_completed == QUOTA_ABORT_AFTER
    assert exc.value.results is results  # partial run is preserved


def test_a_successful_call_resets_the_streak():
    guard = QuotaGuard()
    results: list[RowResult] = []
    for i in range(QUOTA_ABORT_AFTER - 1):
        row = _row(i, instagram=_quota_hit())
        results.append(row)
        guard.observe(row, results)

    # One real answer proves the API is alive — the streak restarts.
    ok = _row(98, instagram=PlatformResult(verdict=Verdict.PASS))
    results.append(ok)
    guard.observe(ok, results)

    # So the next failure must not trip the abort.
    after = _row(99, instagram=_quota_hit())
    results.append(after)
    guard.observe(after, results)  # no raise


def test_skipped_platforms_do_not_reset_the_streak():
    # "No account here" says nothing about the API's health, so a run of rows
    # with no Instagram must not mask an ongoing outage.
    guard = QuotaGuard()
    results: list[RowResult] = []
    for i in range(QUOTA_ABORT_AFTER - 1):
        row = _row(i, instagram=_quota_hit())
        results.append(row)
        guard.observe(row, results)

    blank = _row(50, instagram=PlatformResult(verdict=Verdict.SKIPPED, note="no instagram"))
    results.append(blank)
    guard.observe(blank, results)

    final = _row(51, instagram=_quota_hit())
    results.append(final)
    with pytest.raises(QuotaExhausted):
        guard.observe(final, results)


def test_streaks_are_tracked_per_platform():
    # One platform's failures must never count toward another's threshold.
    # Each row: one platform quota-fails, the other gives a genuine answer
    # (resetting it), so neither ever reaches a full consecutive streak.
    guard = QuotaGuard()
    results: list[RowResult] = []
    genuine = PlatformResult(verdict=Verdict.FAIL)
    for i in range(QUOTA_ABORT_AFTER * 2):
        row = (_row(i, instagram=_quota_hit(), tiktok=genuine) if i % 2
               else _row(i, tiktok=_quota_hit(), instagram=genuine))
        results.append(row)
        guard.observe(row, results)  # no raise


# --- end-to-end through the dataframe runner -------------------------------


class _DeadInstagram:
    """A ScrapeCreators client whose plan ran out after the first row."""

    def __init__(self) -> None:
        self.calls = 0

    def check_instagram(self, handle, *, now):
        self.calls += 1
        if self.calls == 1:  # the first lookup succeeds and passes
            return PlatformResult(handle=handle.value, followers=9_000,
                                  avg_views=5_000.0, verdict=Verdict.PASS)
        return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW,
                              note="api error: 402", error_status=402)

    def check_tiktok(self, handle, *, now):
        return PlatformResult(verdict=Verdict.SKIPPED)


def _frame(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "YouTube": [None] * n,
        "Instagram": [f"user{i}" for i in range(n)],
        "TikTok": [None] * n,
        "LinkedIn": [None] * n,
        "Your country/region": ["Spain"] * n,
    })


def test_run_aborts_but_keeps_the_rows_that_passed():
    df = _frame(20)
    client = _DeadInstagram()
    with pytest.raises(QuotaExhausted) as exc:
        run_over_dataframe(df, resolve_columns(df), social_client=client)

    results = exc.value.results
    # Stopped early rather than grinding through all 20 rows.
    assert len(results) == 1 + QUOTA_ABORT_AFTER < 20
    assert exc.value.platform == "instagram"

    # The row that passed before the credits died is still shown.
    styler = passing_frame(df.astype("string").fillna(""), results)
    assert list(styler.data["Row"]) == [2]  # first data row of the sheet


def test_healthy_run_is_unaffected():
    df = _frame(5)
    results = run_over_dataframe(df, resolve_columns(df))  # no clients at all
    assert len(results) == 5  # nothing aborts
