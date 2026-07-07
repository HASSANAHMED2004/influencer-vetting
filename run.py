"""CLI entry point: vet a batch of rows and write an annotated workbook.

Examples
--------
Free-only dry run (geo screen + normalize + YouTube), rows 2-301:
    python run.py --input VETO.xlsx --start-row 2 --end-row 301

Full run including paid Instagram/TikTok (needs SCRAPECREATORS_API_KEY):
    python run.py --input VETO.xlsx --start-row 2 --end-row 301 --paid
"""

from __future__ import annotations

import argparse
import logging
import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from vetting import config
from vetting.brightdata import BrightDataLinkedInClient
from vetting.models import ExclusionRule, MatchMode
from vetting.pipeline import process_row
from vetting.scrapecreators import ScrapeCreatorsClient
from vetting.sheet_io import read_rows, write_results
from vetting.youtube import YouTubeClient

logger = logging.getLogger("vetting")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vet a batch of influencer rows.")
    parser.add_argument("--input", default="VETO.xlsx", help="input .xlsx path")
    parser.add_argument("--output", default=None,
                        help="output .xlsx path (default: outputs/<input>_vetted_<range>.xlsx)")
    parser.add_argument("--start-row", type=int, default=2, help="first data row (1-based)")
    parser.add_argument("--end-row", type=int, default=None, help="last row (inclusive)")
    parser.add_argument("--paid", action="store_true",
                        help="enable paid Instagram/TikTok checks via ScrapeCreators")
    parser.add_argument(
        "--exclude-country", action="append", default=[], metavar="V1,V2",
        help="disregard rows whose country of residence exactly equals any value "
             "(repeatable, case-insensitive).",
    )
    parser.add_argument(
        "--exclude-country-contains", action="append", default=[], metavar="S1,S2",
        help="disregard rows whose country of residence contains any substring.",
    )
    parser.add_argument("--no-default-exclusions", action="store_true",
                        help="skip the built-in country exclusion from config.py")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _values(specs: list[str]) -> frozenset[str]:
    """Flatten repeated comma-separated CLI specs into a value set."""
    return frozenset(v.strip() for spec in specs for v in spec.split(",") if v.strip())


def build_exclusion_rules(args: argparse.Namespace) -> list[ExclusionRule]:
    rules: list[ExclusionRule] = []
    if not args.no_default_exclusions:
        rules.extend(config.EXCLUSION_RULES)
    exact = _values(args.exclude_country)
    if exact:
        rules.append(ExclusionRule(values=exact, mode=MatchMode.EXACT))
    contains = _values(args.exclude_country_contains)
    if contains:
        rules.append(ExclusionRule(values=contains, mode=MatchMode.CONTAINS,
                                   label="country contains"))
    return rules


def build_clients(
    paid: bool,
) -> tuple[YouTubeClient | None, ScrapeCreatorsClient | None, BrightDataLinkedInClient | None]:
    youtube = None
    yt_key = os.getenv("YOUTUBE_API_KEY")
    if yt_key:
        youtube = YouTubeClient(yt_key)
    else:
        logger.warning("YOUTUBE_API_KEY not set — YouTube checks will be skipped.")

    social = linkedin = None
    if paid:
        sc_key = os.getenv("SCRAPECREATORS_API_KEY")
        if sc_key:
            social = ScrapeCreatorsClient(sc_key)
        else:
            logger.warning("--paid set but SCRAPECREATORS_API_KEY missing — IG/TikTok skipped.")
        bd_token = os.getenv("BRIGHTDATA_API_TOKEN")
        if bd_token:
            linkedin = BrightDataLinkedInClient(bd_token, os.getenv("BRIGHTDATA_DATASET_ID"))
        else:
            logger.warning("--paid set but BRIGHTDATA_API_TOKEN missing — LinkedIn skipped.")
    return youtube, social, linkedin


def default_output(input_path: str, start: int, end: int | None) -> str:
    stem = Path(input_path).stem
    end_label = end if end is not None else "end"
    return str(Path("outputs") / f"{stem}_vetted_{start}-{end_label}.xlsx")


def main() -> None:
    args = parse_args()
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    youtube, social, linkedin = build_clients(args.paid)
    rules = build_exclusion_rules(args)
    logger.info("Country exclusion rules active: %s",
                [f"{r.describe()} {sorted(r.values)} ({r.mode.value})" for r in rules])

    rows = read_rows(args.input, args.start_row, args.end_row)
    logger.info("Vetting %d rows (%s..%s)", len(rows), args.start_row,
                args.end_row if args.end_row is not None else "end")

    results = []
    tally: Counter[str] = Counter()
    for row in rows:
        result = process_row(row, youtube_client=youtube, social_client=social,
                             linkedin_client=linkedin, exclusion_rules=rules)
        results.append(result)
        tally[result.overall.value] += 1

    output = args.output or default_output(args.input, args.start_row, args.end_row)
    write_results(args.input, output, results)

    logger.info("Done. Overall verdicts: %s", dict(tally))
    logger.info("Wrote annotated workbook -> %s", output)


if __name__ == "__main__":
    main()
