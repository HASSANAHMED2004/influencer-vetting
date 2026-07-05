"""Durable run history backed by Supabase.

Each finished run is stored as two things:
- a pickled blob in a Storage bucket (`vetting-history`) holding everything the
  UI needs to re-render the run (results, the display frame, the annotated
  .xlsx bytes);
- a lightweight row in the `runs` table (id, timestamp, row range, counts) that
  powers the fast-loading history list without opening every blob.

The store is intentionally optional: if Supabase isn't configured the app just
runs without history. See ``schema`` below for the one-time table/bucket setup.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from typing import Any

BUCKET = "vetting-history"
TABLE = "runs"


@dataclass(frozen=True)
class RunMeta:
    """One row of the history index (what the sidebar list shows)."""

    id: str
    created_at: str
    row_range: str
    vetted: int
    passed: int
    breakdown: str


class HistoryStore:
    """Thin wrapper over the Supabase client for saving/listing/loading runs."""

    def __init__(self, url: str, key: str) -> None:
        # Imported lazily so the app still starts if `supabase` isn't installed.
        from supabase import create_client

        self._client = create_client(url, key)

    def save(self, run_id: str, run_state: dict[str, Any], meta: dict[str, Any]) -> None:
        """Upload the run blob and insert its index row."""
        blob = pickle.dumps(run_state)
        self._client.storage.from_(BUCKET).upload(
            f"{run_id}.pkl",
            blob,
            {"content-type": "application/octet-stream", "upsert": "true"},
        )
        self._client.table(TABLE).insert({"id": run_id, **meta}).execute()

    def list(self) -> list[RunMeta]:
        """Return saved runs, newest first."""
        resp = (
            self._client.table(TABLE)
            .select("id, created_at, row_range, vetted, passed, breakdown")
            .order("created_at", desc=True)
            .execute()
        )
        return [RunMeta(**row) for row in resp.data]

    def load(self, run_id: str) -> dict[str, Any]:
        """Download and unpickle a run's full render state."""
        blob = self._client.storage.from_(BUCKET).download(f"{run_id}.pkl")
        return pickle.loads(blob)  # noqa: S301 - we only ever load our own blobs

    def delete(self, run_id: str) -> None:
        """Remove both the blob and the index row."""
        self._client.storage.from_(BUCKET).remove([f"{run_id}.pkl"])
        self._client.table(TABLE).delete().eq("id", run_id).execute()


# One-time setup to run in the Supabase SQL editor (create the index table).
# The `vetting-history` Storage bucket is created separately in the dashboard.
SCHEMA_SQL = """
create table if not exists runs (
  id          text primary key,
  created_at  timestamptz not null default now(),
  row_range   text,
  vetted      int,
  passed      int,
  breakdown   text
);
"""
