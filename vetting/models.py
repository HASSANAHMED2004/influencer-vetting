"""Shared data types used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class MatchMode(StrEnum):
    """How an exclusion rule compares the country cell against its values."""

    EXACT = "exact"  # cell equals a value (after trim)
    CONTAINS = "contains"  # value appears as a substring of the cell


@dataclass(frozen=True)
class ExclusionRule:
    """Disregard a row when its country-of-residence value matches any listed value.

    Exclusion is hardcoded to the country column; a rule only carries the values
    to match. Matching is case-insensitive unless ``case_sensitive`` is set.
    ``label`` is a human-friendly name used in notes.
    """

    values: frozenset[str]
    mode: MatchMode = MatchMode.EXACT
    case_sensitive: bool = False
    label: str = "country of residence"

    def matches(self, country_value: object) -> str | None:
        """Return the configured value that matched, or None if the row is kept."""
        if country_value is None:
            return None
        text = str(country_value).strip()
        if not text:
            return None
        hay = text if self.case_sensitive else text.casefold()
        for value in self.values:
            needle = value if self.case_sensitive else value.casefold()
            if not needle:
                continue
            if self.mode is MatchMode.EXACT and hay == needle:
                return value
            if self.mode is MatchMode.CONTAINS and needle in hay:
                return value
        return None

    def describe(self) -> str:
        return self.label


class HandleStatus(StrEnum):
    """Outcome of normalizing a raw handle/link cell."""

    OK = "ok"  # a usable handle was extracted
    MISSING = "missing"  # blank or junk (e.g. "NA", "yes")
    UNRESOLVABLE = "unresolvable"  # present but needs a human (e.g. linktr.ee link)


class Verdict(StrEnum):
    """Per-platform or overall vetting outcome."""

    PASS = "pass"
    FAIL = "fail"
    REVIEW = "review"  # cannot decide automatically; needs a human
    SKIPPED = "skipped"  # no account on this platform, or step disabled
    EXCLUDED = "excluded"  # removed by the geographic screen


@dataclass(frozen=True)
class Handle:
    """A normalized social handle extracted from one spreadsheet cell."""

    platform: str
    raw: str | None
    value: str | None  # canonical lowercase handle, if resolved
    url: str | None  # canonical profile URL, if it can be built
    status: HandleStatus


@dataclass(frozen=True)
class VideoStat:
    """A single video/reel's view count and metadata, as needed for averaging."""

    play_count: int
    created_at: datetime | None
    is_pinned: bool = False


@dataclass(frozen=True)
class PlatformResult:
    """Metrics + verdict for one platform for one influencer."""

    handle: str | None = None
    followers: int | None = None
    # Primary averaged metric for the platform: median recent views for
    # IG/TikTok, max video views for YouTube.
    avg_views: float | None = None
    verdict: Verdict = Verdict.SKIPPED
    note: str = ""


@dataclass
class RowResult:
    """Everything computed for a single spreadsheet row, ready to be written back."""

    row_index: int
    country: str | None = None
    country_status: str = ""
    youtube: PlatformResult = field(default_factory=PlatformResult)
    instagram: PlatformResult = field(default_factory=PlatformResult)
    tiktok: PlatformResult = field(default_factory=PlatformResult)
    linkedin: PlatformResult = field(default_factory=PlatformResult)
    overall: Verdict = Verdict.REVIEW
    notes: list[str] = field(default_factory=list)
