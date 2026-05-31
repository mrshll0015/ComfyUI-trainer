from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS generations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  workflow TEXT NOT NULL,
  file_path TEXT NOT NULL,
  file_sha256 TEXT,
  run_json TEXT,
  notes TEXT,
  prompt_id TEXT,
  media_type TEXT DEFAULT 'image'
);

CREATE TABLE IF NOT EXISTS ratings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  generation_id INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  overall INTEGER NOT NULL CHECK (overall BETWEEN 1 AND 10),
  face INTEGER NOT NULL CHECK (face BETWEEN 1 AND 10),
  motion INTEGER NOT NULL CHECK (motion BETWEEN 1 AND 10),
  lighting INTEGER NOT NULL CHECK (lighting BETWEEN 1 AND 10),
  artifacts INTEGER NOT NULL CHECK (artifacts BETWEEN 1 AND 10),
  identity INTEGER NOT NULL CHECK (identity BETWEEN 1 AND 10),
  hands INTEGER NOT NULL DEFAULT 5 CHECK (hands BETWEEN 1 AND 10),
  fingers INTEGER NOT NULL DEFAULT 5 CHECK (fingers BETWEEN 1 AND 10),
  body INTEGER NOT NULL DEFAULT 5 CHECK (body BETWEEN 1 AND 10),
  skin_tone INTEGER NOT NULL DEFAULT 5 CHECK (skin_tone BETWEEN 1 AND 10),
  comment TEXT
);

CREATE INDEX IF NOT EXISTS idx_generations_workflow_created ON generations(workflow, created_at);
CREATE INDEX IF NOT EXISTS idx_ratings_generation ON ratings(generation_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_generations_prompt ON generations(prompt_id)
  WHERE prompt_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS batch_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  workflow TEXT NOT NULL,
  prompt_profile TEXT NOT NULL,
  count INTEGER NOT NULL,
  image_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  queued INTEGER NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  synced INTEGER NOT NULL DEFAULT 0,
  prompt_ids TEXT,
  error TEXT
);
"""

MIGRATIONS = [
    "ALTER TABLE generations ADD COLUMN prompt_id TEXT",
    "ALTER TABLE generations ADD COLUMN media_type TEXT DEFAULT 'image'",
    "ALTER TABLE ratings ADD COLUMN hands INTEGER NOT NULL DEFAULT 5 CHECK (hands BETWEEN 1 AND 10)",
    "ALTER TABLE ratings ADD COLUMN fingers INTEGER NOT NULL DEFAULT 5 CHECK (fingers BETWEEN 1 AND 10)",
    "ALTER TABLE ratings ADD COLUMN body INTEGER NOT NULL DEFAULT 5 CHECK (body BETWEEN 1 AND 10)",
    "ALTER TABLE ratings ADD COLUMN skin_tone INTEGER NOT NULL DEFAULT 5 CHECK (skin_tone BETWEEN 1 AND 10)",
]


@dataclass(frozen=True)
class Rating:
    overall: int
    face: int
    motion: int
    lighting: int
    artifacts: int
    identity: int
    hands: int = 5
    fingers: int = 5
    body: int = 5
    skin_tone: int = 5
    comment: str = ""


RATING_FIELDS = (
    "overall",
    "face",
    "identity",
    "hands",
    "fingers",
    "body",
    "skin_tone",
    "motion",
    "lighting",
    "artifacts",
)


def _default_db_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "ratings.sqlite")


def _migrate(conn: sqlite3.Connection) -> None:
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or _default_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _migrate(conn)
    return conn


def insert_generation(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    file_path: str,
    file_sha256: Optional[str],
    run_json: Optional[Dict[str, Any]],
    notes: str = "",
    prompt_id: Optional[str] = None,
    media_type: str = "image",
) -> int:
    if prompt_id:
        row = conn.execute(
            "SELECT id FROM generations WHERE prompt_id = ?",
            (prompt_id,),
        ).fetchone()
        if row:
            return int(row["id"])

    created_at = int(time.time())
    run_json_text = json.dumps(run_json, ensure_ascii=False) if run_json is not None else None
    cur = conn.execute(
        """
        INSERT INTO generations(
          created_at, workflow, file_path, file_sha256, run_json, notes, prompt_id, media_type
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            workflow,
            file_path,
            file_sha256,
            run_json_text,
            notes,
            prompt_id,
            media_type,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def insert_rating(conn: sqlite3.Connection, *, generation_id: int, rating: Rating) -> int:
    existing = conn.execute(
        "SELECT id FROM ratings WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE ratings SET
              created_at = ?,
              overall = ?, face = ?, motion = ?, lighting = ?, artifacts = ?, identity = ?,
              hands = ?, fingers = ?, body = ?, skin_tone = ?,
              comment = ?
            WHERE generation_id = ?
            """,
            (
                int(time.time()),
                rating.overall,
                rating.face,
                rating.motion,
                rating.lighting,
                rating.artifacts,
                rating.identity,
                rating.hands,
                rating.fingers,
                rating.body,
                rating.skin_tone,
                rating.comment,
                generation_id,
            ),
        )
        conn.commit()
        return int(existing["id"])

    created_at = int(time.time())
    cur = conn.execute(
        """
        INSERT INTO ratings(
          generation_id, created_at,
          overall, face, motion, lighting, artifacts, identity,
          hands, fingers, body, skin_tone,
          comment
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generation_id,
            created_at,
            rating.overall,
            rating.face,
            rating.motion,
            rating.lighting,
            rating.artifacts,
            rating.identity,
            rating.hands,
            rating.fingers,
            rating.body,
            rating.skin_tone,
            rating.comment,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_generations(
    conn: sqlite3.Connection,
    *,
    workflow: Optional[str] = None,
    pending_only: bool = False,
    limit: int = 50,
) -> List[sqlite3.Row]:
    where = ["1=1"]
    params: List[Any] = []
    if workflow:
        where.append("g.workflow = ?")
        params.append(workflow)
    if pending_only:
        where.append("r.id IS NULL")
    params.append(limit)
    return conn.execute(
        f"""
        SELECT
          g.*,
          r.overall, r.face, r.identity, r.hands, r.fingers, r.body, r.skin_tone,
          r.motion, r.lighting, r.artifacts, r.comment AS rating_comment,
          r.created_at AS rated_at
        FROM generations g
        LEFT JOIN ratings r ON r.generation_id = g.id
        WHERE {' AND '.join(where)}
        ORDER BY g.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def rated_runs(conn: sqlite3.Connection, workflow: str) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
          g.id AS generation_id,
          g.created_at,
          g.workflow,
          g.file_path,
          g.run_json,
          g.prompt_id,
          r.overall, r.face, r.motion, r.lighting, r.artifacts, r.identity,
          r.hands, r.fingers, r.body, r.skin_tone,
          (
            r.overall * 3 + r.identity * 2 + r.face * 2 + r.hands + r.fingers +
            r.body + r.skin_tone + r.motion + r.lighting + r.artifacts
          ) AS weighted_score
        FROM generations g
        JOIN ratings r ON r.generation_id = g.id
        WHERE g.workflow = ? AND g.run_json IS NOT NULL
        ORDER BY weighted_score DESC, g.created_at DESC
        """,
        (workflow,),
    ).fetchall()


def count_pending(
    conn: sqlite3.Connection,
    *,
    workflow: Optional[str] = None,
) -> Dict[str, int]:
    where = ["r.id IS NULL"]
    params: List[Any] = []
    if workflow:
        where.append("g.workflow = ?")
        params.append(workflow)

    row = conn.execute(
        f"""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN g.media_type = 'video' THEN 1 ELSE 0 END) AS videos,
          SUM(CASE WHEN g.media_type != 'video' THEN 1 ELSE 0 END) AS images
        FROM generations g
        LEFT JOIN ratings r ON r.generation_id = g.id
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "videos": int(row["videos"] or 0),
        "images": int(row["images"] or 0),
    }


def insert_batch_run(
    conn: sqlite3.Connection,
    *,
    workflow: str,
    prompt_profile: str,
    count: int,
    image_name: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO batch_runs(created_at, workflow, prompt_profile, count, image_name, status)
        VALUES(?, ?, ?, ?, ?, 'queued')
        """,
        (int(time.time()), workflow, prompt_profile, count, image_name),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_batch_run(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    status: Optional[str] = None,
    queued: Optional[int] = None,
    completed: Optional[int] = None,
    synced: Optional[int] = None,
    prompt_ids: Optional[List[str]] = None,
    error: Optional[str] = None,
) -> None:
    fields: List[str] = []
    params: List[Any] = []
    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if queued is not None:
        fields.append("queued = ?")
        params.append(queued)
    if completed is not None:
        fields.append("completed = ?")
        params.append(completed)
    if synced is not None:
        fields.append("synced = ?")
        params.append(synced)
    if prompt_ids is not None:
        fields.append("prompt_ids = ?")
        params.append(json.dumps(prompt_ids))
    if error is not None:
        fields.append("error = ?")
        params.append(error)
    if not fields:
        return
    params.append(batch_id)
    conn.execute(f"UPDATE batch_runs SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
