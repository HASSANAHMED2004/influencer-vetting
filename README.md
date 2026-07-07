# Influencer Vetting Pipeline

Semi-automated vetting of a spreadsheet of social-media influencers. Processes a
batch of rows at a time, applies the numeric criteria automatically, and leaves
the content/copyright/NSFW judgement calls for a human.

## What it checks

| Step | Platform | Cost | Rule |
|------|----------|------|------|
| Geographic screen | — | free | Exclude rows whose **country of residence** (self-reported) is one of the configured countries |
| YouTube | YouTube | **free** (Data API) | Pass if **any** video ≥ 100k views |
| Instagram | Instagram | paid | ≥ 1,000 followers **and** median recent-Reel views ≥ 3,000 |
| TikTok | TikTok | paid | ≥ 1,000 followers **and** median recent-video views ≥ 3,000 |
| LinkedIn | LinkedIn | paid (Bright Data) | ≥ 800 followers (followers-only) |

LinkedIn goes through **Bright Data**, not ScrapeCreators — ScrapeCreators is
~89% blind on LinkedIn (private/unavailable profiles come back 404), whereas
Bright Data recovers most of them. Public LinkedIn post-engagement is too
inconsistent to gate on, so LinkedIn is judged on follower count alone.

**"Average views" methodology:** the median play count of the most recent ~12
videos, excluding pinned videos and anything posted in the last 48h. Median (not
mean) so one viral hit or one dud doesn't skew it; recent-only so early
low-view videos don't punish an improving creator. See `vetting/config.py` to tune.

Qualifying is **OR** across platforms (`QUALIFY_ON_ANY_PLATFORM`): passing on any
one platform approves the row.

**Check order (saves paid credits, `SHORT_CIRCUIT_ON_PASS`):**
1. **YouTube** is always checked — it's free — and runs **independently**. A
   YouTube pass does *not* skip the paid checks; they run side by side.
2. The paid platforms are tried in priority order — **Instagram → TikTok →
   LinkedIn** — and the moment one of *them* passes, the remaining paid platforms
   are **skipped** (marked `not checked (already qualified)`), so no further
   credits are spent on that row. (LinkedIn is only reached when both IG and
   TikTok fail, so its slower Bright Data calls happen rarely.)

Content vetting (religious/political/offensive, real-actor copyright, NSFW) is
**not** automated — it stays a manual pass on the rows that clear the numbers.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # installs deps + pytest/ruff
cp .env.example .env           # then fill in your API keys
```

- **`YOUTUBE_API_KEY`** — free, from Google Cloud Console (enable "YouTube Data API v3").
- **`SCRAPECREATORS_API_KEY`** — optional/paid, from https://scrapecreators.com.
  Without it, Instagram/TikTok are skipped and marked *not-checked*.
- **`BRIGHTDATA_API_TOKEN`** — optional, from brightdata.com (5k free records/mo).
  Powers the LinkedIn check; without it, LinkedIn is skipped. Optionally set
  **`BRIGHTDATA_DATASET_ID`** (defaults to the LinkedIn people-profiles scraper).

## Run

Process a batch by row range (1-based, inclusive). Free-only dry run first:

```bash
python run.py --input VETO.xlsx --start-row 2 --end-row 301
```

Then a full batch including the paid Instagram/TikTok checks:

```bash
python run.py --input VETO.xlsx --start-row 2 --end-row 301 --paid
```

Output is written to `outputs/VETO_vetted_<range>.xlsx` with new columns
(`vet_country_status`, per-platform followers/views/verdict, `overall_verdict`,
`notes`) and column A ticked where `overall_verdict == pass`. Re-run per batch:
`2–301`, `302–601`, and so on.

## Country exclusion (disregard rows by country of residence)

Exclusion is hardcoded to the **country column**. Give a set of country values
and matching rows are dropped up front (marked `excluded`) before any lookups, so
you never pay to vet them. The built-in list lives in `EXCLUSION_RULES` in
`vetting/config.py`.

Add ad-hoc country values on the CLI (case-insensitive, repeatable):

```bash
# Drop exact country values (in addition to the config defaults):
python run.py --input VETO.xlsx --exclude-country "India,Pakistan"

# Drop by substring (e.g. anything containing "korea"):
python run.py --input VETO.xlsx --exclude-country-contains "korea"

# Turn OFF the built-in country exclusion:
python run.py --input VETO.xlsx --no-default-exclusions
```

`--exclude-country` matches the whole country cell; `--exclude-country-contains`
matches substrings. Both can be given multiple times and combine with the config
defaults. The `notes` column records which value triggered each exclusion. For
permanent rules, add `ExclusionRule(...)` entries to `EXCLUSION_RULES` in
`config.py`.

## Verdict meanings

- **pass** – clears the numeric bar (checkbox ticked; ready for manual content review).
- **fail** – below a threshold.
- **review** – couldn't decide automatically (e.g. an unresolvable `linktr.ee`
  link, a photo-only IG account with no Reels, or an API error). **Look at these by hand.**
- **excluded** – removed by the geographic screen.
- **skipped** – the influencer has no account on that platform.

## Tests

```bash
pytest        # unit tests for normalization, metrics, and orchestration
ruff check .  # lint
```

Tests cover the pure logic (handle/country normalization, the median-views rule,
threshold decisions, geo screen). The network clients are thin and gated behind
API keys, so they aren't exercised in the unit suite.

## Important caveats

- **Personal data.** `VETO.xlsx` holds ~4,000 real people's emails/names/handles.
  It's gitignored; keep it out of version control and handle accordingly.
- **ScrapeCreators endpoints are verified live (2026-07-05).** Instagram is one
  call (followers + embedded recent-media views); TikTok is two (profile +
  `/v3/tiktok/profile/videos`). Parsers live in `vetting/scrapecreators.py`;
  if schemas drift, that's the only file to adjust.
- **YouTube quota.** The channel scan is bounded to the most recent
  `MAX_VIDEOS_TO_SCAN` (200) uploads to stay within the free daily quota; a viral
  video older than that window would be missed. Raise the cap if needed.
- **Geo screen is only as good as the country field**, which is self-reported
  residence — an Indian creator living in Berlin reads as "Germany".
