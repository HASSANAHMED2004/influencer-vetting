"""Tests for row-level orchestration: geo screen and verdict combination."""

from __future__ import annotations

from dataclasses import dataclass

from vetting.models import ExclusionRule, MatchMode, PlatformResult, Verdict
from vetting.pipeline import decide_overall, process_row


@dataclass
class FakeRow:
    row_index: int
    youtube: object = None
    instagram: object = None
    tiktok: object = None
    linkedin: object = None
    country: object = None


def test_filtered_country_is_excluded_before_any_lookup():
    result = process_row(FakeRow(2, instagram="someone", country="India"))
    assert result.overall is Verdict.EXCLUDED
    # No metrics fetched — excluded rows never reach the clients.
    assert result.instagram.followers is None


def test_blank_country_is_flagged_not_excluded():
    result = process_row(FakeRow(3, instagram="someone", country=None))
    assert result.overall is not Verdict.EXCLUDED
    assert "country-unknown" in result.notes


def test_unresolvable_handle_is_noted():
    result = process_row(FakeRow(4, instagram="https://linktr.ee/x", country="Spain"))
    assert any("needs manual check" in n for n in result.notes)


def test_custom_country_contains_rule():
    # Disregard rows whose country contains a banned substring.
    rule = ExclusionRule(values=frozenset({"korea"}), mode=MatchMode.CONTAINS,
                         label="country contains")
    row = FakeRow(5, instagram="someone", country="South Korea")
    result = process_row(row, exclusion_rules=[rule])
    assert result.overall is Verdict.EXCLUDED
    assert any("country contains" in n for n in result.notes)


def test_exclusion_rules_can_be_disabled():
    # With no rules, a normally-excluded country is kept.
    result = process_row(FakeRow(6, instagram="someone", country="India"),
                         exclusion_rules=[])
    assert result.overall is not Verdict.EXCLUDED


class FakeYouTube:
    def __init__(self, verdict=Verdict.FAIL):
        self.verdict = verdict

    def check(self, handle):
        return PlatformResult(handle=handle.value, verdict=self.verdict)


class FakeSocial:
    """Records which paid platforms were actually called."""

    def __init__(self, ig=Verdict.FAIL, tt=Verdict.FAIL, li=Verdict.FAIL):
        self.calls: list[str] = []
        self._verdicts = {"instagram": ig, "tiktok": tt, "linkedin": li}

    def _call(self, platform, handle):
        self.calls.append(platform)
        return PlatformResult(handle=handle.value, verdict=self._verdicts[platform])

    def check_instagram(self, handle, *, now):
        return self._call("instagram", handle)

    def check_tiktok(self, handle, *, now):
        return self._call("tiktok", handle)

    def check_linkedin(self, handle, *, now):
        return self._call("linkedin", handle)


def _full_row():
    return FakeRow(2, youtube="chan", instagram="ig_user", tiktok="tt_user",
                   country="Spain")


def test_instagram_pass_short_circuits_tiktok_and_linkedin():
    social = FakeSocial(ig=Verdict.PASS)
    result = process_row(_full_row(), youtube_client=FakeYouTube(Verdict.FAIL),
                         social_client=social)
    assert result.overall is Verdict.PASS
    assert social.calls == ["instagram"]  # tiktok/linkedin never called
    assert result.tiktok.note == "not checked (already qualified)"
    assert result.linkedin.note == "not checked (already qualified)"


def test_youtube_pass_does_not_skip_paid_chain():
    # YouTube runs side by side: even though it passes, the paid chain still runs.
    social = FakeSocial(ig=Verdict.FAIL, tt=Verdict.FAIL)
    result = process_row(_full_row(), youtube_client=FakeYouTube(Verdict.PASS),
                         social_client=social)
    assert result.overall is Verdict.PASS  # YouTube alone qualifies it
    assert social.calls == ["instagram", "tiktok", "linkedin"]  # all still checked


def test_disabled_platform_is_never_called():
    social = FakeSocial(ig=Verdict.FAIL, tt=Verdict.PASS)
    result = process_row(_full_row(), youtube_client=FakeYouTube(Verdict.FAIL),
                         social_client=social, disabled_platforms=("tiktok",))
    assert "tiktok" not in social.calls  # never called
    assert result.tiktok.verdict is Verdict.SKIPPED
    assert result.tiktok.note == "tiktok disabled"
    assert result.overall is Verdict.FAIL  # TikTok would've passed, but it's off


def test_falls_through_to_tiktok_when_instagram_fails():
    social = FakeSocial(ig=Verdict.FAIL, tt=Verdict.PASS)
    result = process_row(_full_row(), youtube_client=FakeYouTube(Verdict.FAIL),
                         social_client=social)
    assert result.overall is Verdict.PASS
    assert social.calls == ["instagram", "tiktok"]  # linkedin skipped after tiktok pass


def test_decide_overall_any_platform():
    passing = PlatformResult(verdict=Verdict.PASS)
    failing = PlatformResult(verdict=Verdict.FAIL)
    skipped = PlatformResult(verdict=Verdict.SKIPPED)
    assert decide_overall([passing, failing, skipped], any_platform=True) is Verdict.PASS
    assert decide_overall([failing, skipped], any_platform=True) is Verdict.FAIL
    assert decide_overall([skipped, skipped], any_platform=True) is Verdict.REVIEW
