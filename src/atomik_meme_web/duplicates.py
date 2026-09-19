"""Duplicate detection: exact groups by sha256, near groups by dHash banding.

Near-duplicate candidates are found via 4 bands of 16 bits each (a classic LSH trick for
Hamming-distance search): two items sharing any one band are compared with a full 64-bit
Hamming distance, and unioned into a cluster when within `max_distance`. Pairs that are already
exact duplicates (same sha256) are skipped when forming near clusters. Results are cached per
scan id so repeated `/api/duplicates` calls between scans are free.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, datetime

from atomik_meme_web.search import fetch_rows_by_ids, row_to_item

BAND_COUNT = 4
BAND_HEX_WIDTH = 4  # 16 bits per band, 4 hex chars
_HASH_HEX_LEN = 16  # 64 bits

# A flat/degenerate frame (solid colour, or any image whose horizontal gradients all point the
# same way) hashes to all-0 or all-1 bits regardless of what colour it actually is -- grouping on
# that would cluster together completely unrelated flat-coloured memes. Exact sha256 groups are
# unaffected (they never depend on dhash).
_DEGENERATE_HASHES = frozenset({"0" * _HASH_HEX_LEN, "f" * _HASH_HEX_LEN})


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_exact_groups(conn: sqlite3.Connection) -> list[dict]:
    """All `(sha256, id)` pairs for sha256 values shared by 2+ items, in two queries total."""
    dup_shas = [
        row["sha256"]
        for row in conn.execute(
            "SELECT sha256 FROM items GROUP BY sha256 HAVING COUNT(*) > 1"
        ).fetchall()
    ]
    if not dup_shas:
        return []
    placeholders = ",".join("?" for _ in dup_shas)
    rows = conn.execute(
        f"SELECT id, sha256 FROM items WHERE sha256 IN ({placeholders}) ORDER BY sha256, id",
        dup_shas,
    ).fetchall()
    groups_by_sha: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        groups_by_sha[row["sha256"]].append(row["id"])
    return [{"kind": "exact", "distance": 0, "ids": ids} for ids in groups_by_sha.values()]


def _find_near_groups(conn: sqlite3.Connection, max_distance: int) -> list[dict]:
    rows = [
        row
        for row in conn.execute(
            "SELECT id, sha256, dhash FROM items WHERE dhash IS NOT NULL"
        ).fetchall()
        if row["dhash"] not in _DEGENERATE_HASHES
    ]
    by_id = {row["id"]: row for row in rows}

    band_index: dict[tuple[int, str], list[int]] = defaultdict(list)
    for row in rows:
        h = row["dhash"]
        for band_i in range(BAND_COUNT):
            start = band_i * BAND_HEX_WIDTH
            band_val = h[start : start + BAND_HEX_WIDTH]
            band_index[(band_i, band_val)].append(row["id"])

    parent: dict[int, int] = {row["id"]: row["id"] for row in rows}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    checked: set[tuple[int, int]] = set()
    for candidates in band_index.values():
        if len(candidates) < 2:
            continue
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = candidates[i], candidates[j]
                pair = (min(a, b), max(a, b))
                if pair in checked:
                    continue
                checked.add(pair)
                if by_id[a]["sha256"] == by_id[b]["sha256"]:
                    continue  # already an exact duplicate, not a "near" one
                dist = _hamming(by_id[a]["dhash"], by_id[b]["dhash"])
                if dist <= max_distance:
                    union(a, b)

    clusters: dict[int, list[int]] = defaultdict(list)
    for item_id in parent:
        clusters[find(item_id)].append(item_id)

    groups = []
    for ids in clusters.values():
        if len(ids) < 2:
            continue
        max_dist = 0
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                max_dist = max(max_dist, _hamming(by_id[ids[i]]["dhash"], by_id[ids[j]]["dhash"]))
        groups.append({"kind": "near", "distance": max_dist, "ids": sorted(ids)})
    return groups


class DuplicatesService:
    def __init__(self, db):
        self.db = db
        self._cache: dict[tuple, dict] = {}

    def _current_scan_id(self) -> int:
        row = self.db.conn.execute("SELECT id FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        return row["id"] if row is not None else 0

    def compute(self, near: bool, max_distance: int) -> dict:
        scan_id = self._current_scan_id()
        key = (scan_id, near, max_distance)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        conn = self.db.conn
        groups_raw = _find_exact_groups(conn)
        if near:
            groups_raw.extend(_find_near_groups(conn, max_distance))

        all_ids = {i for g in groups_raw for i in g["ids"]}
        rows_by_id = fetch_rows_by_ids(conn, list(all_ids))
        groups = []
        for g in groups_raw:
            items = [row_to_item(rows_by_id[i]) for i in g["ids"] if i in rows_by_id]
            if len(items) > 1:
                groups.append({"kind": g["kind"], "distance": g["distance"], "items": items})

        result = {"groups": groups, "computed_at": _utcnow_iso()}
        self._cache = {key: result}
        return result

    def invalidate(self) -> None:
        self._cache = {}
