"""Streamlit UI for influencer vetting.

Upload a dataset -> Run -> the same dataset back, with passing rows color-filled
by which platform qualified them. Non-passing rows are left untouched.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os
from io import BytesIO

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from vetting.frames import (
    MULTI_STYLE,
    PLATFORM_STYLES,
    MissingColumnsError,
    color_summary,
    resolve_columns,
    run_over_dataframe,
    style_dataframe,
    write_colored_xlsx,
)
from vetting.scrapecreators import ScrapeCreatorsClient
from vetting.youtube import YouTubeClient

load_dotenv()
st.set_page_config(page_title="Influencer Vetting", page_icon="🎯", layout="wide")

_LABEL_STYLE = {s.label: s for s in [*PLATFORM_STYLES.values(), MULTI_STYLE]}

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stMarkdown, .stButton, .stDownloadButton {
  font-family: 'Inter', sans-serif; }
.stApp { background:
  linear-gradient(160deg,#FBF3FF 0%,#F1F6FF 42%,#EDFCF6 100%); }
.block-container { padding-top: 2.2rem; max-width: 1180px; }
[data-testid="stHeader"] { background: transparent; }
.hero h1 { font-size: 2.7rem; font-weight: 800; margin: 0; letter-spacing: -0.02em;
  background: linear-gradient(90deg,#7C3AED,#EC4899,#F59E0B);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.hero p { color:#5B6472; font-size:1.05rem; margin:.35rem 0 1rem; }
[data-testid="stVerticalBlockBorderWrapper"] { background:#FFFFFF; border-radius:18px;
  box-shadow:0 8px 30px rgba(90,60,180,.10); border:1px solid #ECE6F8 !important; }
.stButton>button, .stDownloadButton>button {
  border-radius:11px; font-weight:600; padding:.55rem 1.25rem; }
.stButton>button[kind="primary"] { background:linear-gradient(90deg,#7C3AED,#EC4899);
  border:0; color:#fff; box-shadow:0 8px 20px rgba(124,58,237,.34); }
.stButton>button[kind="primary"]:hover { filter:brightness(1.07); }
.stDownloadButton>button { background:linear-gradient(90deg,#10B981,#06B6D4);
  border:0; color:#fff; box-shadow:0 8px 20px rgba(6,182,212,.30); }
.pill { display:inline-block; padding:5px 14px; border-radius:999px; font-size:.8rem;
  font-weight:600; margin:3px 6px 3px 0; box-shadow:0 2px 7px rgba(0,0,0,.13); }
.statgrid { display:flex; gap:16px; flex-wrap:wrap; margin:.5rem 0 .5rem; }
.stat { border-radius:18px; padding:18px 26px; min-width:150px; color:#fff;
  box-shadow:0 10px 26px rgba(90,60,180,.18); }
.stat .n { font-size:2.1rem; font-weight:800; line-height:1; }
.stat .l { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em;
  margin-top:7px; opacity:.92; }
.stat.a { background:linear-gradient(135deg,#8B5CF6,#EC4899); }
.stat.b { background:linear-gradient(135deg,#3B82F6,#06B6D4); }
.stat.c { background:linear-gradient(135deg,#F59E0B,#F97316); }
[data-testid="stDataFrame"] { border-radius:14px; overflow:hidden;
  border:1px solid #E7E1F4; box-shadow:0 6px 22px rgba(90,60,180,.10); }
.muted { color:#5B6472; font-size:.88rem; }
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


def _secret(name: str, default: str | None = None) -> str | None:
    """Read config from Streamlit secrets first, then the environment (.env)."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass  # no secrets.toml present (e.g. local dev)
    return os.getenv(name, default)


def _require_login() -> None:
    """Simple shared-password gate. Open if APP_PASSWORD isn't configured."""
    expected = _secret("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return
    st.markdown('<div class="hero"><h1>Influencer Vetting</h1></div>', unsafe_allow_html=True)
    st.text_input("Password", type="password", key="_pw")
    if st.session_state.get("_pw"):
        if st.session_state["_pw"] == expected:
            st.session_state["authed"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


def _pills(items: list[tuple[str, str, str, str]]) -> str:
    """items: (text, fill, font, _) -> a run of colored pills as HTML."""
    return "".join(
        f'<span class="pill" style="background:{fill};color:{font}">{text}</span>'
        for text, fill, font, _ in items
    )


def _legend() -> None:
    pills = _pills([(s.label, s.fill, s.font, "") for s in _LABEL_STYLE.values()])
    st.markdown(
        f'<div style="margin:.1rem 0 .5rem"><span class="muted" style="margin-right:8px">'
        f'Pass colors</span>{pills}</div>', unsafe_allow_html=True,
    )
    st.markdown('<span class="muted">Multiple platforms passing → yellow. '
                'Rows that don\'t pass are left blank.</span>', unsafe_allow_html=True)


_require_login()

st.markdown(
    '<div class="hero"><h1>Influencer Vetting</h1>'
    '<p>Upload a dataset, run the checks, and get passing rows highlighted by platform.</p></div>',
    unsafe_allow_html=True,
)

# --- Controls ---
with st.container(border=True):
    uploaded = st.file_uploader("Dataset (.xlsx)", type=["xlsx"],
                                help="Needs YouTube, Instagram, TikTok, LinkedIn and "
                                     "country columns. Extra columns are fine.")
    c1, c2 = st.columns(2)
    run_paid = c1.toggle("Paid checks (Instagram / TikTok)", value=True,
                         help="YouTube is always checked for free. Off = YouTube-only.")
    include_linkedin = c2.toggle("Include LinkedIn", value=False, disabled=not run_paid,
                                 help="LinkedIn is the most credit-heavy platform and often "
                                      "unavailable (404s). Off by default.")

if uploaded is None:
    st.info("⬆️ Upload an .xlsx to begin.")
    st.stop()

raw_bytes = uploaded.getvalue()
df = pd.read_excel(BytesIO(raw_bytes)).reset_index(drop=True)

try:
    colmap = resolve_columns(df)
except MissingColumnsError as exc:
    st.error(str(exc))
    st.stop()

total_rows = len(df)
matched = " · ".join(f"{k} → “{v}”" for k, v in colmap.items())
st.markdown(
    f'<span class="muted">✅ Loaded <b>{total_rows:,}</b> rows &nbsp;•&nbsp; {matched}</span>',
    unsafe_allow_html=True,
)

if total_rows > 1:
    start_row, end_row = st.slider(
        "Rows to vet", 1, total_rows, (1, total_rows),
        help="Only these rows are checked (saves API credits). Others stay blank.",
    )
else:
    start_row, end_row = 1, total_rows
st.markdown(f'<span class="muted">Will vet rows {start_row}–{end_row} '
            f'({end_row - start_row + 1:,} rows).</span>', unsafe_allow_html=True)

_legend()

if not st.button("▶  Run vetting", type="primary"):
    st.stop()

run_df = df.iloc[start_row - 1:end_row]
disabled_platforms = () if include_linkedin else ("linkedin",)

# Build API clients from secrets (Streamlit Cloud) or environment (.env).
youtube_client = None
if yt_key := _secret("YOUTUBE_API_KEY"):
    youtube_client = YouTubeClient(yt_key)
else:
    st.warning("No YOUTUBE_API_KEY set — YouTube will not be checked.")

social_client = None
if run_paid:
    if sc_key := _secret("SCRAPECREATORS_API_KEY"):
        social_client = ScrapeCreatorsClient(sc_key)
    else:
        st.warning("No SCRAPECREATORS_API_KEY set — paid checks skipped.")

progress = st.progress(0.0, text="Vetting rows…")
results = run_over_dataframe(
    run_df, colmap,
    youtube_client=youtube_client, social_client=social_client,
    disabled_platforms=disabled_platforms,
    progress=lambda f: progress.progress(f, text=f"Vetting rows… {int(f * 100)}%"),
)
progress.empty()

# --- Results ---
summary = color_summary(results)
passed = sum(summary.values())
st.markdown(
    '<div class="statgrid">'
    f'<div class="stat a"><div class="n">{passed:,}</div><div class="l">Passed</div></div>'
    f'<div class="stat b"><div class="n">{len(run_df):,}</div><div class="l">Vetted</div></div>'
    f'<div class="stat c"><div class="n">{total_rows:,}</div><div class="l">Total rows</div></div>'
    '</div>', unsafe_allow_html=True,
)
if summary:
    breakdown = _pills([(f"{label}: {n}", _LABEL_STYLE[label].fill, _LABEL_STYLE[label].font, "")
                        for label, n in summary.items()])
    st.markdown(f'<div style="margin:.3rem 0 .6rem">{breakdown}</div>', unsafe_allow_html=True)

st.dataframe(style_dataframe(df, results), use_container_width=True, height=560)

st.download_button(
    "⬇  Download annotated .xlsx",
    data=write_colored_xlsx(raw_bytes, results),
    file_name="vetted_colored.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
