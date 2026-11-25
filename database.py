import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "dance_data.db"


def connect():
    connection = sqlite3.connect(DB_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    return connection


def _decode_song(row):
    if row is None:
        return None
    song = dict(row)
    try:
        song["dance_alternatives"] = json.loads(song.get("dance_alternatives") or "[]")
    except (TypeError, json.JSONDecodeError):
        song["dance_alternatives"] = []
    return song


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

        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(songs)").fetchall()
        }
        migrations = {
            "dance": "ALTER TABLE songs ADD COLUMN dance TEXT",
            "dance_alternatives": "ALTER TABLE songs ADD COLUMN dance_alternatives TEXT",
            "dance_manual": "ALTER TABLE songs ADD COLUMN dance_manual INTEGER NOT NULL DEFAULT 0",
            "bpm_manual": "ALTER TABLE songs ADD COLUMN bpm_manual INTEGER NOT NULL DEFAULT 0",
            "time_signature_manual": "ALTER TABLE songs ADD COLUMN time_signature_manual INTEGER NOT NULL DEFAULT 0",
            "energy": "ALTER TABLE songs ADD COLUMN energy REAL",
            "danceability": "ALTER TABLE songs ADD COLUMN danceability REAL",
        }
        for column, sql in migrations.items():
            if column not in columns:
                db.execute(sql)

        db.execute("""
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                used INTEGER DEFAULT 0,
                quota_limit INTEGER DEFAULT 1000,
                resets_at TEXT
            )
        """)
        db.execute("""
            INSERT OR IGNORE INTO api_usage (id, used, quota_limit)
            VALUES (1, 0, 1000)
        """)

        # Favoriten bleiben unabhängig von einzelnen Songs gespeichert.
        db.execute("""
            CREATE TABLE IF NOT EXISTS dance_preferences (
                dance TEXT PRIMARY KEY,
                favorite INTEGER NOT NULL DEFAULT 0
            )
        """)


def get_song(isrc):
    with connect() as db:
        return _decode_song(db.execute(
            "SELECT * FROM songs WHERE isrc = ?", (isrc,)
        ).fetchone())


def save_song(song):
    with connect() as db:
        existing = db.execute(
            "SELECT * FROM songs WHERE isrc = ?", (song["isrc"],)
        ).fetchone()

        if existing:
            existing = dict(existing)
            bpm = existing["bpm"] if existing.get("bpm_manual") else song.get("bpm")
            signature = (
                existing["time_signature"]
                if existing.get("time_signature_manual")
                else song.get("time_signature")
            )
            db.execute("""
                UPDATE songs
                SET title=?, artist=?, bpm=?, time_signature=?,
                    tempo_confidence=?, source=?, energy=?, danceability=?, analyzed_at=CURRENT_TIMESTAMP
                WHERE isrc=?
            """, (
                song["title"], song["artist"], bpm, signature,
                song.get("tempo_confidence"), song.get("source"), song.get("energy"),
                song.get("danceability"), song["isrc"]
            ))
        else:
            db.execute("""
                INSERT INTO songs (
                    isrc, title, artist, bpm, time_signature,
                    tempo_confidence, source, energy, danceability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                song["isrc"], song["title"], song["artist"], song.get("bpm"),
                song.get("time_signature"), song.get("tempo_confidence"),
                song.get("source"), song.get("energy"), song.get("danceability")
            ))


def save_dance(isrc, dance, alternatives, manual=False):
    alternatives_json = json.dumps(alternatives or [], ensure_ascii=False)
    with connect() as db:
        if manual:
            db.execute("""
                UPDATE songs
                SET dance=?, dance_alternatives=?, dance_manual=1,
                    analyzed_at=CURRENT_TIMESTAMP
                WHERE isrc=?
            """, (dance, alternatives_json, isrc))
        else:
            db.execute("""
                UPDATE songs
                SET dance=CASE WHEN dance_manual=1 THEN dance ELSE ? END,
                    dance_alternatives=CASE
                        WHEN dance_manual=1 THEN dance_alternatives ELSE ? END,
                    analyzed_at=CURRENT_TIMESTAMP
                WHERE isrc=?
            """, (dance, alternatives_json, isrc))


def update_song_values(isrc, bpm=None, time_signature=None,
                       dance=None, alternatives=None,
                       update_tempo=True, update_dance=True, manual_dance=True):
    alternatives_json = json.dumps(alternatives or [], ensure_ascii=False)
    with connect() as db:
        row = db.execute("SELECT * FROM songs WHERE isrc=?", (isrc,)).fetchone()
        if not row:
            return False

        current = dict(row)
        new_bpm = bpm if update_tempo else current["bpm"]
        new_meter = time_signature if update_tempo else current["time_signature"]
        new_dance = dance if update_dance else current.get("dance")
        new_alternatives = alternatives_json if update_dance else (
            current.get("dance_alternatives") or "[]"
        )

        db.execute("""
            UPDATE songs
            SET bpm=?, time_signature=?, dance=?, dance_alternatives=?,
                bpm_manual=CASE WHEN ? THEN 1 ELSE bpm_manual END,
                time_signature_manual=CASE WHEN ? THEN 1 ELSE time_signature_manual END,
                dance_manual=CASE WHEN ? THEN 1 WHEN ? THEN 0 ELSE dance_manual END,
                analyzed_at=CURRENT_TIMESTAMP
            WHERE isrc=?
        """, (
            new_bpm, new_meter, new_dance, new_alternatives,
            int(update_tempo), int(update_tempo), int(update_dance and manual_dance),
            int(update_dance and not manual_dance), isrc
        ))
        return True


def get_all_songs():
    with connect() as db:
        return [_decode_song(row) for row in db.execute(
            "SELECT * FROM songs ORDER BY analyzed_at DESC"
        ).fetchall()]


def set_favorite(dance, favorite):
    with connect() as db:
        db.execute("""
            INSERT INTO dance_preferences (dance, favorite)
            VALUES (?, ?)
            ON CONFLICT(dance) DO UPDATE SET favorite=excluded.favorite
        """, (dance, int(bool(favorite))))


def get_favorites():
    with connect() as db:
        rows = db.execute(
            "SELECT dance FROM dance_preferences WHERE favorite=1"
        ).fetchall()
        return [row["dance"] for row in rows]


def get_preferences():
    with connect() as db:
        rows = db.execute("SELECT dance, favorite FROM dance_preferences").fetchall()
        return {row["dance"]: bool(row["favorite"]) for row in rows}


def update_usage(used, limit, resets_at=None):
    with connect() as db:
        db.execute("""
            UPDATE api_usage SET used=?, quota_limit=?, resets_at=? WHERE id=1
        """, (used, limit, resets_at))


def get_usage():
    with connect() as db:
        row = db.execute("SELECT * FROM api_usage WHERE id=1").fetchone()
        return dict(row) if row else {"used": 0, "quota_limit": 1000, "resets_at": None}


def get_song_count():
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
