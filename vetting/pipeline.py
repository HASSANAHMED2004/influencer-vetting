"""Per-row orchestration: geo screen -> normalize -> YouTube -> IG/TikTok -> verdict.

The decision logic (`decide_overall`) is pure and testable; `process_row` wires
the clients to it. Either client may be None (e.g. no API key / free-only run),
in which case that platform is skipped.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from typing import Protocol

from . import config
from .config import QUALIFY_ON_ANY_PLATFORM, SHORT_CIRCUIT_ON_PASS
from .exclusions import first_matching_rule
from .models import ExclusionRule, Handle, HandleStatus, PlatformResult, RowResult, Verdict
from .normalize import (
    normalize_country,
    normalize_instagram,
    normalize_tiktok,
    normalize_youtube,
)


class InfluencerRow(Protocol):
    """Minimal shape the pipeline needs from a spreadsheet row."""

    row_index: int
    youtube: object
    instagram: object
    tiktok: object
    country: object


class _YouTubeLike(Protocol):
    def check(self, handle: Handle) -> PlatformResult: ...


class _SocialLike(Protocol):
    def check_instagram(self, handle: Handle, *, now: datetime) -> PlatformResult: ...
    def check_tiktok(self, handle: Handle, *, now: datetime) -> PlatformResult: ...


def decide_overall(results: list[PlatformResult], *, any_platform: bool) -> Verdict:
    """Combine per-platform verdicts into one overall verdict.

    Only platforms the influencer is actually on (not SKIPPED) count. With
    ``any_platform`` True, one PASS qualifies them (for manual content review);
    otherwise every present platform must PASS. A present-but-undecidable
    platform yields REVIEW.
    """
    present = [r.verdict for r in results if r.verdict is not Verdict.SKIPPED]
    if not present:
        return Verdict.REVIEW  # no usable accounts to judge on
    if any_platform:
        if Verdict.PASS in present:
            return Verdict.PASS
        return Verdict.REVIEW if Verdict.REVIEW in present else Verdict.FAIL
    # Strict: all present platforms must pass.
    if any(v is Verdict.FAIL for v in present):
        return Verdict.FAIL
    if any(v is Verdict.REVIEW for v in present):
        return Verdict.REVIEW
    return Verdict.PASS


def process_row(
    row: InfluencerRow,
    *,
    youtube_client: _YouTubeLike | None = None,
    social_client: _SocialLike | None = None,
    exclusion_rules: Sequence[ExclusionRule] | None = None,
    disabled_platforms: Collection[str] = (),
    now: datetime | None = None,
) -> RowResult:
    """Run the full per-row pipeline and return a RowResult ready to write back.

    ``disabled_platforms`` (e.g. {"tiktok"}) are skipped entirely — not called,
    not counted — regardless of clients.
    """
    now = now or datetime.now(UTC)
    if exclusion_rules is None:
        exclusion_rules = config.EXCLUSION_RULES
    result = RowResult(row_index=row.row_index, country=normalize_country(row.country))

    # 1. Country exclusion screen — cheap and first, so we never pay to vet a
    #    dropped row. Hardcoded to the country column.
    hit = first_matching_rule(row.country, exclusion_rules)
    if hit is not None:
        rule, cell = hit
        result.country_status = "excluded"
        result.overall = Verdict.EXCLUDED
        result.notes.append(f"excluded ({rule.describe()}): {str(cell).strip()!r}")
        return result

    result.country_status = "unknown" if result.country is None else "ok"
    if result.country is None:
        result.notes.append("country-unknown")

    # 2. Normalize handles and collect any that need a human.
    yt_handle = normalize_youtube(row.youtube)
    ig_handle = normalize_instagram(row.instagram)
    tt_handle = normalize_tiktok(row.tiktok)
    for h in (yt_handle, ig_handle, tt_handle):
        if h.status is HandleStatus.UNRESOLVABLE:
            result.notes.append(f"{h.platform} link needs manual check: {h.raw}")

    # 3. YouTube (free) — always checked, independently, for every row with a
    #    channel. A YouTube pass does NOT skip the paid chain; they run side by side.
    if youtube_client is not None:
        result.youtube = youtube_client.check(yt_handle)
    else:
        result.youtube = _skipped_or_review(yt_handle, "youtube step disabled")

    # 4. Paid platforms in priority order — Instagram, then TikTok —
    #    short-circuiting once one of *them* passes, to save API credits. This chain
    #    runs regardless of the YouTube result.
    short_circuit = SHORT_CIRCUIT_ON_PASS and QUALIFY_ON_ANY_PLATFORM
    paid_approved = False
    paid_steps = (
        ("instagram", ig_handle, "check_instagram", "instagram not-checked (no api key)"),
        ("tiktok", tt_handle, "check_tiktok", "tiktok not-checked (no api key)"),
    )
    for attr, handle, method, disabled_note in paid_steps:
        if attr in disabled_platforms:
            setattr(result, attr, _disabled(handle))
            continue
        if short_circuit and paid_approved:
            setattr(result, attr, _already_qualified(handle))
            continue
        if social_client is not None:
            outcome = getattr(social_client, method)(handle, now=now)
        else:
            outcome = _skipped_or_review(handle, disabled_note)
        setattr(result, attr, outcome)
        if outcome.verdict is Verdict.PASS:
            paid_approved = True

    # 5. Combine.
    result.overall = decide_overall(
        [result.youtube, result.instagram, result.tiktok],
        any_platform=QUALIFY_ON_ANY_PLATFORM,
    )
    return result


def _skipped_or_review(handle: Handle, disabled_note: str) -> PlatformResult:
    """When a client isn't available: SKIP if there's no account, else REVIEW."""
    if handle.status is HandleStatus.MISSING:
        return PlatformResult(verdict=Verdict.SKIPPED, note=f"no {handle.platform}")
    return PlatformResult(handle=handle.value, verdict=Verdict.REVIEW, note=disabled_note)


def _already_qualified(handle: Handle) -> PlatformResult:
    """A paid platform we deliberately skipped because the row already passed."""
    if handle.status is HandleStatus.MISSING:
        return PlatformResult(verdict=Verdict.SKIPPED, note=f"no {handle.platform}")
    return PlatformResult(handle=handle.value, verdict=Verdict.SKIPPED,
                          note="not checked (already qualified)")


def _disabled(handle: Handle) -> PlatformResult:
    """A platform the caller turned off for this run."""
    if handle.status is HandleStatus.MISSING:
        return PlatformResult(verdict=Verdict.SKIPPED, note=f"no {handle.platform}")
    return PlatformResult(handle=handle.value, verdict=Verdict.SKIPPED,
                          note=f"{handle.platform} disabled")
