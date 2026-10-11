"""
Local persistent storage for perceptual hashes.
Tracks pHash per job in jobs/hashes.json.
Used for near-duplicate detection across uploads.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
try:
    import imagehash
    from PIL import Image
    _IMAGEHASH_OK = True
except ImportError:
    _IMAGEHASH_OK = False

from requirements_config import DEFAULT_PROFILE

BASE_DIR        = Path(__file__).resolve().parent
STORE_PATH      = BASE_DIR / "jobs" / "hashes.json"
# Local internal heuristic threshold for pHash Hamming distance; NOT an official Displate threshold
HASH_THRESHOLD  = DEFAULT_PROFILE.phash_distance_threshold
HASH_TTL_HOURS  = 7 * 24  # Keep hash entries 7 days (longer than job cleanup)


# ── Internal I/O ───────────────────────────────────────────────────────────────

def available() -> bool:
    return _IMAGEHASH_OK


def _load() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict):
    STORE_PATH.parent.mkdir(exist_ok=True)
    STORE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _phash(image_path: Path) -> str | None:
    if not _IMAGEHASH_OK:
        return None
    try:
        with Image.open(image_path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def store_hash(job_id: str, image_path: Path, width: int, height: int) -> str | None:
    """Compute pHash for image and persist it under job_id."""
    h = _phash(image_path)
    if not h:
        return None
    data = _load()
    data[job_id] = {
        "phash":    h,
        "filename": image_path.name,
        "width":    width,
        "height":   height,
        "ts":       time.time(),
    }
    _save(data)
    return h


def find_duplicates(
    current_job_id: str,
    image_path: Path,
    threshold: int = HASH_THRESHOLD,
) -> list[dict]:
    """
    Compare image against all stored hashes.
    Returns list of near-duplicate matches sorted by Hamming distance (closest first).
    Each match: {job_id, distance, similarity (%), filename, width, height, preview_url}
    """
    if not _IMAGEHASH_OK:
        return []
    try:
        with Image.open(image_path) as img:
            query_hash = imagehash.phash(img)
    except Exception:
        return []

    data    = _load()
    matches = []
    for job_id, entry in data.items():
        if job_id == current_job_id:
            continue
        try:
            stored   = imagehash.hex_to_hash(entry["phash"])
            distance = int(query_hash - stored)
            if distance <= threshold:
                matches.append({
                    "job_id":      job_id,
                    "distance":    distance,
                    "similarity":  round((1 - distance / 64) * 100, 1),
                    "filename":    entry.get("filename", ""),
                    "width":       entry.get("width"),
                    "height":      entry.get("height"),
                    "preview_url": f"/media/{job_id}/{entry.get('filename', '')}",
                })
        except Exception:
            continue

    matches.sort(key=lambda x: x["distance"])
    return matches


def delete_hash(job_id: str):
    """Remove a job's hash entry from the store."""
    data = _load()
    if job_id in data:
        del data[job_id]
        _save(data)


def cleanup_old_hashes(ttl_hours: int = HASH_TTL_HOURS):
    """Prune entries older than ttl_hours."""
    data    = _load()
    cutoff  = time.time() - ttl_hours * 3600
    cleaned = {k: v for k, v in data.items() if v.get("ts", 0) > cutoff}
    if len(cleaned) != len(data):
        _save(cleaned)
