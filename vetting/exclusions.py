"""Applying exclusion rules (hardcoded to the country column). Pure logic."""

from __future__ import annotations

from collections.abc import Sequence

from .models import ExclusionRule


def first_matching_rule(
    country_value: object, rules: Sequence[ExclusionRule]
) -> tuple[ExclusionRule, object] | None:
    """Return the first rule that fires against the country value, else None.

    Rules are checked in order, so the first match wins (useful for its note/label).
    """
    for rule in rules:
        if rule.matches(country_value) is not None:
            return rule, country_value
    return None
