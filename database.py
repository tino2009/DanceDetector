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
            "valence": "ALTER TABLE songs ADD COLUMN valence REAL",
            "acousticness": "ALTER TABLE songs ADD COLUMN acousticness REAL",
            "loudness": "ALTER TABLE songs ADD COLUMN loudness REAL",
            "instrumentalness": "ALTER TABLE songs ADD COLUMN instrumentalness REAL",
            "speechiness": "ALTER TABLE songs ADD COLUMN speechiness REAL",
            "liveness": "ALTER TABLE songs ADD COLUMN liveness REAL",
            "musical_key": "ALTER TABLE songs ADD COLUMN musical_key TEXT",
            "key_confidence": "ALTER TABLE songs ADD COLUMN key_confidence REAL",
            "spotify_track_id": "ALTER TABLE songs ADD COLUMN spotify_track_id TEXT",
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
        db.execute("""
            CREATE TABLE IF NOT EXISTS classifier_weights (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                bpm REAL NOT NULL DEFAULT 80,
                energy REAL NOT NULL DEFAULT 12,
                danceability REAL NOT NULL DEFAULT 10,
                valence REAL NOT NULL DEFAULT 5,
                acousticness REAL NOT NULL DEFAULT 5,
                speechiness REAL NOT NULL DEFAULT 3,
                instrumentalness REAL NOT NULL DEFAULT 5,
                liveness REAL NOT NULL DEFAULT 2,
                loudness REAL NOT NULL DEFAULT 5
            )
        """)
        weight_columns = {row["name"] for row in db.execute("PRAGMA table_info(classifier_weights)").fetchall()}
        for column in ("valence", "acousticness", "speechiness", "instrumentalness", "liveness", "loudness"):
            if column not in weight_columns:
                default = {"valence": 5, "acousticness": 5, "speechiness": 3, "instrumentalness": 5, "liveness": 2, "loudness": 5}[column]
                db.execute(f"ALTER TABLE classifier_weights ADD COLUMN {column} REAL NOT NULL DEFAULT {default}")
        db.execute("INSERT OR IGNORE INTO classifier_weights (id) VALUES (1)")
        legacy = db.execute("SELECT bpm, energy, danceability FROM classifier_weights WHERE id=1").fetchone()
        if legacy and (legacy["bpm"], legacy["energy"], legacy["danceability"]) == (80.0, 12.0, 8.0):
            db.execute("""UPDATE classifier_weights SET bpm=55, energy=10, danceability=10,
                valence=5, acousticness=5, speechiness=3, instrumentalness=5,
                liveness=2, loudness=5 WHERE id=1""")
        db.execute("""CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        db.execute("""INSERT OR IGNORE INTO app_settings(key, value)
            VALUES ('analysis_provider', 'melodata')""")
        db.execute("""INSERT OR IGNORE INTO app_settings(key, value)
            VALUES ('use_database_cache', '1')""")


def get_song(isrc):
    with connect() as db:
        return _decode_song(db.execute(
            "SELECT * FROM songs WHERE isrc = ?", (isrc,)
        ).fetchone())


def save_song(song):
    """Speichert Merkmale, ohne manuelle BPM-/Taktkorrekturen zu überschreiben."""
    if not song.get("isrc"):
        raise ValueError("Song benötigt eine ISRC.")
    title = song.get("title") or "Unbekannter Titel"
    artist = song.get("artist") or "Unbekannter Interpret"
    feature_values = (
        song.get("tempo_confidence"), song.get("source"), song.get("energy"),
        song.get("danceability"), song.get("valence"), song.get("acousticness"),
        song.get("loudness"), song.get("instrumentalness"), song.get("speechiness"),
        song.get("liveness"), song.get("key"), song.get("key_confidence")
    )
    with connect() as db:
        existing = db.execute("SELECT * FROM songs WHERE isrc=?", (song["isrc"],)).fetchone()
        if existing:
            existing = dict(existing)
            bpm = existing["bpm"] if existing.get("bpm_manual") else song.get("bpm")
            meter = existing["time_signature"] if existing.get("time_signature_manual") else song.get("time_signature")
            db.execute("""
                UPDATE songs SET title=?, artist=?, bpm=?, time_signature=?,
                    tempo_confidence=?, source=?, energy=?, danceability=?,
                    valence=?, acousticness=?, loudness=?, instrumentalness=?,
                    speechiness=?, liveness=?, musical_key=?, key_confidence=?,
                    spotify_track_id=COALESCE(?, spotify_track_id),
                    analyzed_at=CURRENT_TIMESTAMP WHERE isrc=?
            """, (title, artist, bpm, meter, *feature_values, song.get("spotify_track_id"), song["isrc"]))
        else:
            db.execute("""
                INSERT INTO songs (
                    isrc,title,artist,bpm,time_signature,tempo_confidence,source,
                    energy,danceability,valence,acousticness,loudness,instrumentalness,
                    speechiness,liveness,musical_key,key_confidence,spotify_track_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (song["isrc"],title,artist,song.get("bpm"),song.get("time_signature"),
                  *feature_values, song.get("spotify_track_id")))


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


def save_manual_song(title, artist, bpm, time_signature, energy=None, danceability=None, dance=None):
    """Speichert einen Song ohne MeloData/ISRC mit einer lokalen Kennung."""
    import uuid
    local_id = "MANUAL-" + uuid.uuid4().hex
    with connect() as db:
        db.execute("""
            INSERT INTO songs (
                isrc, title, artist, bpm, time_signature, energy, danceability,
                dance, dance_alternatives, dance_manual, bpm_manual,
                time_signature_manual, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, 1, 1, 'manual')
        """, (local_id, title, artist, bpm, time_signature, energy,
              danceability, dance, int(bool(dance))))
    return local_id


def get_song_by_title_artist(title, artist):
    """Lokaler Cache-Lookup vor jedem MeloData-Aufruf (ohne API-Verbrauch)."""
    with connect() as db:
        row = db.execute("""
            SELECT * FROM songs
            WHERE lower(trim(title)) = lower(trim(?))
              AND lower(trim(artist)) = lower(trim(?))
            ORDER BY CASE WHEN source='manual' OR isrc LIKE 'MANUAL-%' THEN 0 ELSE 1 END,
                     analyzed_at DESC
            LIMIT 1
        """, (title, artist)).fetchone()
        return _decode_song(row)


def get_classifier_weights():
    with connect() as db:
        row = db.execute("SELECT bpm, energy, danceability, valence, acousticness, speechiness, instrumentalness, liveness, loudness FROM classifier_weights WHERE id=1").fetchone()
        return dict(row) if row else {"bpm": 55.0, "energy": 10.0, "danceability": 10.0, "valence": 5.0, "acousticness": 5.0, "speechiness": 3.0, "instrumentalness": 5.0, "liveness": 2.0, "loudness": 5.0}


def set_classifier_weights(**weights):
    columns = ("bpm", "energy", "danceability", "valence", "acousticness", "speechiness", "instrumentalness", "liveness", "loudness")
    values = [float(weights[name]) for name in columns]
    with connect() as db:
        db.execute("UPDATE classifier_weights SET " + ", ".join(f"{name}=?" for name in columns) + " WHERE id=1", values)


def get_setting(key, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with connect() as db:
        db.execute("""
            INSERT INTO app_settings(key,value) VALUES(?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def get_dance_rules(default_rules):
    """Lädt die benutzerdefinierten Tanzprofile aus den App-Einstellungen."""
    raw = get_setting("dance_rules")
    if not raw:
        return [dict(item) for item in default_rules]
    try:
        saved = json.loads(raw)
        if not isinstance(saved, list):
            raise ValueError
        defaults = {item["name"]: dict(item) for item in default_rules}
        result = []
        for item in saved:
            if isinstance(item, dict) and item.get("name") in defaults:
                merged = {**defaults[item["name"]], **item}
                result.append(merged)
        # Neue Tänze aus späteren Programmversionen ergänzen.
        present = {item["name"] for item in result}
        result.extend(item for name, item in defaults.items() if name not in present)
        return result
    except (TypeError, ValueError, json.JSONDecodeError):
        return [dict(item) for item in default_rules]


def save_dance_rules(rules):
    """Speichert alle Tanzprofile dauerhaft in app_settings."""
    set_setting("dance_rules", json.dumps(rules, ensure_ascii=False))
    return True


def get_use_database_cache():
    return get_setting("use_database_cache", "1") == "1"


def set_use_database_cache(enabled):
    set_setting("use_database_cache", "1" if enabled else "0")
