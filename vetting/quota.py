"""Detecting an exhausted API plan and aborting the run early.

Without this, a drained API key is indistinguishable from a private account:
every lookup fails, every platform is marked REVIEW, and the run finishes
looking "complete" while silently checking nobody. Worse, rows vetted after the
credits died are dropped from the passing table with no explanation.

So we watch for the HTTP statuses that mean "your plan/quota is the problem"
(401/402/403/429) and abort once one platform returns them ``QUOTA_ABORT_AFTER``
times in a row. Consecutive — a single blip or one rate-limited call resets the
streak, so only a persistent outage stops the run.

The abort carries the results gathered so far, so the caller can still show and
download everyone who passed before the credits ran out.
"""

from __future__ import annotations

from .config import QUOTA_ABORT_AFTER, QUOTA_ERROR_STATUSES
from .models import RowResult, Verdict


class QuotaExhausted(RuntimeError):
    """Raised when a platform's API keeps reporting a plan/quota failure.

    Carries ``results`` — every row completed before the abort — so a partial
    run is still usable rather than thrown away.
    """

    def __init__(self, platform: str, status: int, results: list[RowResult],
                 rows_completed: int) -> None:
        self.platform = platform
        self.status = status
        self.results = results
        self.rows_completed = rows_completed
        super().__init__(
            f"{platform} API returned HTTP {status} "
            f"{QUOTA_ABORT_AFTER} times in a row — the key is out of credits, "
            f"rate-limited, or unauthorized. Stopped after {rows_completed} rows."
        )


def http_status(exc: Exception) -> int | None:
    """The HTTP status behind a requests error, if there was a response."""
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) if response is not None else None


def is_quota_error(exc: Exception) -> bool:
    """True if this failure looks like a plan/quota problem, not a bad profile."""
    return http_status(exc) in QUOTA_ERROR_STATUSES


class QuotaGuard:
    """Tracks consecutive quota failures per platform across a run."""

    def __init__(self, threshold: int = QUOTA_ABORT_AFTER) -> None:
        self._threshold = threshold
        self._streaks: dict[str, int] = {}

    def observe(self, result: RowResult, results: list[RowResult]) -> None:
        """Inspect one finished row; raise QuotaExhausted if a platform is done.

        ``results`` is the full list gathered so far, attached to the exception
        so the caller keeps the partial run.
        """
        for platform, outcome in (
            ("youtube", result.youtube),
            ("instagram", result.instagram),
            ("tiktok", result.tiktok),
            ("linkedin", result.linkedin),
        ):
            status = outcome.error_status
            if status in QUOTA_ERROR_STATUSES:
                streak = self._streaks.get(platform, 0) + 1
                self._streaks[platform] = streak
                if streak >= self._threshold:
                    raise QuotaExhausted(platform, status, results, len(results))
            elif outcome.verdict is not Verdict.SKIPPED:
                # A genuine answer (the API is alive) — the streak is broken.
                # SKIPPED means "no account here", which tells us nothing.
                self._streaks[platform] = 0


__all__ = ["QuotaExhausted", "QuotaGuard", "http_status", "is_quota_error"]
