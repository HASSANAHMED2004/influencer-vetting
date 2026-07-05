"""Tests for the country-based exclusion feature."""

from __future__ import annotations

from vetting.exclusions import first_matching_rule
from vetting.models import ExclusionRule, MatchMode


def test_exact_match_is_case_insensitive_and_trimmed():
    rule = ExclusionRule(values=frozenset({"India", "Pakistan"}))
    assert rule.matches("  india ") == "India"
    assert rule.matches("PAKISTAN") == "Pakistan"
    assert rule.matches("Indialand") is None  # exact, not substring
    assert rule.matches(None) is None
    assert rule.matches("") is None


def test_contains_match():
    rule = ExclusionRule(values=frozenset({"korea"}), mode=MatchMode.CONTAINS)
    assert rule.matches("South Korea") == "korea"
    assert rule.matches("Spain") is None


def test_case_sensitive_rule():
    rule = ExclusionRule(values=frozenset({"Iran"}), case_sensitive=True)
    assert rule.matches("Iran") == "Iran"
    assert rule.matches("iran") is None


def test_first_matching_rule_returns_first_and_stops():
    rules = [
        ExclusionRule(values=frozenset({"India"}), label="country of residence"),
        ExclusionRule(values=frozenset({"ind"}), mode=MatchMode.CONTAINS, label="contains"),
    ]
    hit = first_matching_rule("India", rules)
    assert hit is not None
    rule, value = hit
    assert rule.label == "country of residence"  # first rule wins
    assert value == "India"


def test_first_matching_rule_none_when_clean():
    rules = [ExclusionRule(values=frozenset({"India"}))]
    assert first_matching_rule("Spain", rules) is None
    assert first_matching_rule(None, rules) is None
