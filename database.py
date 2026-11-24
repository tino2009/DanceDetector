
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "dance_data.db"


def connect():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                isrc TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                bpm REAL,
                time_signature INTEGER,
                tempo_confidence REAL,
                source TEXT,
                analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                used INTEGER DEFAULT 0,
                quota_limit INTEGER DEFAULT 1000,
                resets_at TEXT
            )
        """)

        db.execute("""
            INSERT OR IGNORE INTO api_usage
            (id, used, quota_limit)
            VALUES (1, 0, 1000)
        """)


def get_song(isrc):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM songs WHERE isrc = ?",
            (isrc,)
        ).fetchone()

        return dict(row) if row else None


def save_song(song):
    with connect() as db:
        db.execute("""
            INSERT INTO songs (
                isrc, title, artist, bpm,
                time_signature, tempo_confidence, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(isrc) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                bpm = excluded.bpm,
                time_signature = excluded.time_signature,
                tempo_confidence = excluded.tempo_confidence,
                source = excluded.source,
                analyzed_at = CURRENT_TIMESTAMP
        """, (
            song["isrc"],
            song["title"],
            song["artist"],
            song.get("bpm"),
            song.get("time_signature"),
            song.get("tempo_confidence"),
            song.get("source")
        ))


def get_all_songs():
    with connect() as db:
        rows = db.execute("""
            SELECT * FROM songs
            ORDER BY analyzed_at DESC
        """).fetchall()

        return [dict(row) for row in rows]


def update_usage(used, limit, resets_at=None):
    with connect() as db:
        db.execute("""
            UPDATE api_usage
            SET used = ?, quota_limit = ?, resets_at = ?
            WHERE id = 1
        """, (used, limit, resets_at))


def get_usage():
    with connect() as db:
        row = db.execute(
            "SELECT * FROM api_usage WHERE id = 1"
        ).fetchone()

        return dict(row)


def get_song_count():
    with connect() as db:
        return db.execute(
            "SELECT COUNT(*) FROM songs"
        ).fetchone()[0]
