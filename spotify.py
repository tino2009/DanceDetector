import os
import re
import secrets
import threading
import time
import webbrowser

import requests
from flask import Flask, request, jsonify, redirect, render_template
from dotenv import load_dotenv

import melodata
import database
import analysis_provider
from dance_classifier import classify_dance, DANCE_NAMES, DANCE_RULES

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
    raise RuntimeError("Spotify-Zugangsdaten fehlen.")

SCOPE = "user-read-currently-playing playlist-read-private"
app = Flask(__name__)
state = secrets.token_urlsafe(16)

access_token = None
refresh_token = None
token_expires_at = 0
current_song = None
analysis = None
analysis_status = "Warte auf Spotify"
analysis_error = None
last_track_id = None

playlist_status = {
    "running": False, "total": 0, "processed": 0,
    "failed": 0, "message": "Noch keine Playlist gestartet."
}

lock = threading.RLock()
playlist_lock = threading.Lock()
reanalysis_lock = threading.Lock()
reanalysis_status = {
    "running": False, "total": 0, "processed": 0,
    "failed": 0, "skipped": 0, "message": "Noch nicht gestartet."
}


def spotify_headers():
    return {"Authorization": f"Bearer {access_token}"}


def refresh_access_token():
    global access_token, token_expires_at, refresh_token
    if not refresh_token:
        return False
    try:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(CLIENT_ID, CLIENT_SECRET), timeout=15
        )
        if response.status_code != 200:
            print("Spotify Token Refresh fehlgeschlagen:", response.text)
            return False
        data = response.json()
        access_token = data["access_token"]
        token_expires_at = time.time() + data.get("expires_in", 3600)
        refresh_token = data.get("refresh_token", refresh_token)
        return True
    except requests.RequestException as error:
        print("Spotify Refresh Fehler:", error)
        return False


def ensure_access_token():
    if not access_token:
        return False
    return refresh_access_token() if time.time() >= token_expires_at - 60 else True


def get_current_song():
    if not ensure_access_token():
        return None
    try:
        response = requests.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers=spotify_headers(), timeout=15
        )
        if response.status_code == 401:
            if not refresh_access_token():
                return None
            response = requests.get(
                "https://api.spotify.com/v1/me/player/currently-playing",
                headers=spotify_headers(), timeout=15
            )
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            print("Spotify Fehler:", response.status_code, response.text)
            return None
        data = response.json()
        track = data.get("item")
        if not track:
            return None
        return {
            "id": track["id"], "name": track["name"],
            "artists": [a["name"] for a in track.get("artists", [])],
            "is_playing": data.get("is_playing", False)
        }
    except requests.RequestException as error:
        print("Spotify Anfrage fehlgeschlagen:", error)
        return None


def attach_dance_result(result):
    if not isinstance(result, dict):
        raise ValueError("Die Analysequelle hat kein gültiges Ergebnis geliefert.")
    isrc = result.get("isrc")
    saved = database.get_song(isrc) if isrc else None
    if saved and saved.get("dance_manual"):
        dance_result = {
            "best": {"name": saved.get("dance"), "score": None}
                    if saved.get("dance") else None,
            "alternatives": saved.get("dance_alternatives") or [],
            "message": None if saved.get("dance") else "Noch kein Tanz zugeordnet.",
            "manual": True
        }
    else:
        dance_result = classify_dance(
            result.get("bpm"), result.get("time_signature"),
            favorites=database.get_favorites(),
            weights=database.get_classifier_weights(),
            **{key: result.get(key) for key in ("energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness")}
        )
        if isrc:
            database.save_dance(
                isrc,
                dance_result["best"]["name"] if dance_result["best"] else None,
                dance_result["alternatives"],
                manual=False
            )
        dance_result["manual"] = False
    result["dance"] = dance_result
    return result


def get_song_data_without_api(title, artist, force_refresh=False, provider=None, spotify_track_id=None):
    """Optional zuerst den lokalen Cache prüfen; nur bei Cache-Miss den Provider aufrufen."""
    selected_provider = provider or analysis_provider.get_provider()
    if database.get_use_database_cache() and not force_refresh:
        cached = database.get_song_by_title_artist(title, artist)
        if cached and cached.get("bpm") is not None and cached.get("time_signature") is not None:
            cached["cached"] = True
            cached["cache_source"] = cached.get("source") or "Datenbank"
            return cached
    return analysis_provider.analyze_song(
        title, artist, force_refresh=force_refresh,
        provider=selected_provider, spotify_track_id=spotify_track_id
    )


def persist_song_result(result, preferred_isrc=None):
    """Speichert jede erfolgreiche Analyse und aktualisiert vorhandene Songs statt Duplikate anzulegen."""
    if not isinstance(result, dict) or not result.get("isrc"):
        raise ValueError("Analyseergebnis enthält keine gültige Song-ID.")
    track_id = result.get("spotify_track_id")
    existing = database.get_song(preferred_isrc) if preferred_isrc else None
    if not existing:
        existing = database.get_song_by_title_artist(result.get("title", ""), result.get("artist", ""))
    if existing and result.get("source") == "musicstax":
        # MeloData-ISRC beibehalten, damit der Song nicht doppelt in der Bibliothek erscheint.
        result["isrc"] = existing["isrc"]
    if track_id:
        result["spotify_track_id"] = track_id
    database.save_song(result)
    return result


def find_spotify_track_id(title, artist):
    """Ermittelt bei älteren Bibliothekseinträgen die Spotify-ID über die Spotify-Suche."""
    if not ensure_access_token():
        raise RuntimeError("Spotify ist nicht verbunden; Track-ID kann nicht ermittelt werden.")
    response = requests.get(
        "https://api.spotify.com/v1/search",
        headers=spotify_headers(),
        params={"q": f'track:"{title}" artist:"{artist}"', "type": "track", "limit": 5},
        timeout=20,
    )
    if response.status_code == 401 and refresh_access_token():
        response = requests.get(
            "https://api.spotify.com/v1/search", headers=spotify_headers(),
            params={"q": f'track:"{title}" artist:"{artist}"', "type": "track", "limit": 5},
            timeout=20,
        )
    if response.status_code != 200:
        raise RuntimeError(f"Spotify-Suche fehlgeschlagen: HTTP {response.status_code}")
    items = response.json().get("tracks", {}).get("items", [])
    if not items:
        raise RuntimeError("Kein passender Spotify-Track für diesen Bibliothekseintrag gefunden.")
    # Prefer an exact title and primary-artist match; fall back to Spotify's top result.
    wanted_title, wanted_artist = title.casefold().strip(), artist.casefold().strip()
    for item in items:
        artists = [a.get("name", "").casefold() for a in item.get("artists", [])]
        if item.get("name", "").casefold().strip() == wanted_title and wanted_artist in artists:
            return item["id"]
    return items[0]["id"]


def analyze_current_song(song, track_id):
    global analysis, analysis_status, analysis_error
    with lock:
        analysis_status = f"Analysiere Song mit {analysis_provider.get_provider()}..."
        analysis_error = None
        analysis = None
    try:
        result = get_song_data_without_api(song["name"], song["artists"][0], spotify_track_id=track_id)
        result = persist_song_result(result)
        result = attach_dance_result(result)
        with lock:
            if last_track_id != track_id:
                return
            analysis = result
            analysis_status = "Aus lokaler Datenbank" if result.get("cached") else "Analyse abgeschlossen"
    except Exception as error:
        with lock:
            if last_track_id == track_id:
                analysis_error = str(error)
                analysis_status = "Analyse fehlgeschlagen"
        print("Analysefehler:", error)


def spotify_worker():
    global current_song, last_track_id, analysis, analysis_error, analysis_status
    while True:
        try:
            song = get_current_song()
            if song:
                with lock:
                    current_song = song
                if song["id"] != last_track_id:
                    with lock:
                        last_track_id = song["id"]
                        analysis = None
                        analysis_error = None
                        analysis_status = "Neuer Song erkannt"
                    if song.get("artists"):
                        threading.Thread(
                            target=analyze_current_song,
                            args=(song, song["id"]), daemon=True
                        ).start()
            else:
                with lock:
                    current_song = None
                    analysis = None
                    analysis_error = None
                    analysis_status = "Warte auf Spotify"
                last_track_id = None
            time.sleep(1)
        except Exception as error:
            print("Spotify Worker Fehler:", error)
            time.sleep(2)


def extract_playlist_id(url):
    match = re.search(r"(?:playlist/|spotify:playlist:)([a-zA-Z0-9]+)", url.strip())
    if not match:
        raise ValueError("Ungültiger Spotify-Playlist-Link.")
    return match.group(1)


def get_playlist_tracks(playlist_id):
    if not ensure_access_token():
        raise RuntimeError("Spotify ist nicht verbunden oder Token ungültig.")
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
    while url:
        response = requests.get(
            url, headers=spotify_headers(),
            params={"limit": 50} if "?" not in url else None, timeout=20
        )
        if response.status_code == 401 and refresh_access_token():
            response = requests.get(
                url, headers=spotify_headers(),
                params={"limit": 50} if "?" not in url else None, timeout=20
            )
        if response.status_code != 200:
            raise RuntimeError(f"Spotify Playlist-Fehler: {response.status_code} – {response.text}")
        data = response.json()
        for entry in data.get("items", []):
            track = entry.get("item") or entry.get("track")
            if not track or track.get("type") != "track":
                continue
            artists = track.get("artists", [])
            if artists:
                tracks.append({
                    "id": track.get("id"), "name": track.get("name"),
                    "artist": artists[0].get("name")
                })
        url = data.get("next")
    return tracks


def analyze_playlist_worker(playlist_id):
    global playlist_status
    try:
        tracks = get_playlist_tracks(playlist_id)
        unique, seen = [], set()
        for track in tracks:
            track_id = track.get("id")
            if track_id and track_id in seen:
                continue
            if track_id:
                seen.add(track_id)
            unique.append(track)

        with playlist_lock:
            playlist_status = {
                "running": True, "total": len(unique), "processed": 0,
                "failed": 0, "message": "Playlist wird analysiert."
            }

        for index, track in enumerate(unique, start=1):
            try:
                result = get_song_data_without_api(track["name"], track["artist"], spotify_track_id=track.get("id"))
                result = persist_song_result(result)
                attach_dance_result(result)
                with playlist_lock:
                    playlist_status["message"] = f"Analysiere: {track['name']} ({index}/{len(unique)})"
            except Exception as error:
                print(f"Playlist-Analyse fehlgeschlagen für {track['name']}: {error}")
                with playlist_lock:
                    playlist_status["failed"] += 1
            finally:
                with playlist_lock:
                    playlist_status["processed"] += 1
                    playlist_status["total"] = len(unique)

        with playlist_lock:
            playlist_status["running"] = False
            playlist_status["message"] = "Playlist-Analyse abgeschlossen."
    except Exception as error:
        print("Playlist Fehler:", error)
        with playlist_lock:
            playlist_status["running"] = False
            playlist_status["message"] = str(error)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    url = "https://accounts.spotify.com/authorize"
    params = {
        "client_id": CLIENT_ID, "response_type": "code",
        "redirect_uri": REDIRECT_URI, "scope": SCOPE, "state": state
    }
    return redirect(requests.Request("GET", url, params=params).prepare().url)


@app.route("/api/spotify/callback")
def callback():
    global access_token, refresh_token, token_expires_at
    code = request.args.get("code")
    if request.args.get("state") != state:
        return "Spotify State ungültig.", 403
    if not code:
        return "Kein Authorization Code erhalten.", 400
    try:
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
            auth=(CLIENT_ID, CLIENT_SECRET), timeout=15
        )
        if response.status_code != 200:
            return f"Spotify Fehler: {response.text}", 400
        data = response.json()
        access_token = data["access_token"]
        refresh_token = data.get("refresh_token")
        token_expires_at = time.time() + data.get("expires_in", 3600)
        print("Spotify erfolgreich verbunden!")
        return redirect("/")
    except requests.RequestException as error:
        return f"Verbindungsfehler: {error}", 500


@app.route("/api/state")
def api_state():
    with lock:
        current_analysis = analysis.copy() if analysis else None
        if current_analysis and current_analysis.get("isrc") and not (
            current_analysis.get("dance") or {}
        ).get("manual"):
            current_analysis["dance"] = classify_dance(
                current_analysis.get("bpm"),
                current_analysis.get("time_signature"),
                favorites=database.get_favorites(),
                weights=database.get_classifier_weights(),
                **{key: current_analysis.get(key) for key in ("energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness")}
            )
        return jsonify({
            "connected": access_token is not None,
            "song": current_song,
            "analysis": current_analysis,
            "analysis_status": analysis_status,
            "analysis_error": analysis_error,
            "usage": database.get_usage(),
            "song_count": database.get_song_count(),
            "favorites": database.get_favorites()
        })


@app.route("/api/songs")
def api_songs():
    return jsonify(database.get_all_songs())


@app.route("/api/dance-options", methods=["GET"])
def dance_options():
    return jsonify(DANCE_RULES)


@app.route("/api/dance-options/<dance_name>", methods=["POST"])
def update_dance_option(dance_name):
    data = request.get_json(silent=True) or {}
    dance = next((item for item in DANCE_RULES if item["name"] == dance_name), None)
    if dance is None:
        return jsonify({"error": "Unbekannte Tanzart."}), 404
    try:
        updated = dict(dance)
        updated["meter"] = int(data.get("meter", dance["meter"]))
        updated["min"] = float(data.get("min", dance["min"]))
        updated["max"] = float(data.get("max", dance["max"]))
        updated["ideal"] = float(data.get("ideal", dance["ideal"]))
        updated["weight"] = float(data.get("weight", dance["weight"]))
        feature_ranges = {
            "energy": (0, 1), "danceability": (0, 1), "valence": (0, 1),
            "acousticness": (0, 1), "speechiness": (0, 1),
            "instrumentalness": (0, 1), "liveness": (0, 1),
            "loudness": (-60, 0)
        }
        for key, (low, high) in feature_ranges.items():
            updated[key] = float(data.get(key, dance[key]))
            if not low <= updated[key] <= high:
                raise ValueError(f"{key} muss zwischen {low} und {high} liegen.")
        if updated["meter"] < 2 or updated["meter"] > 12:
            raise ValueError("Die Taktart muss zwischen 2 und 12 liegen.")
        if updated["min"] <= 0 or updated["max"] < updated["min"]:
            raise ValueError("Das BPM-Minimum muss positiv und kleiner/gleich dem Maximum sein.")
        if not updated["min"] <= updated["ideal"] <= updated["max"]:
            raise ValueError("Das ideale Tempo muss zwischen Minimum und Maximum liegen.")
        if not 0.1 <= updated["weight"] <= 3:
            raise ValueError("Der Tanzfaktor muss zwischen 0,1 und 3 liegen.")
        dance.update(updated)
        database.save_dance_rules(DANCE_RULES)
        refresh_all_automatic_dances()
        refresh_current_dance()
        return jsonify({"success": True, "dance": dance})
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


def refresh_all_automatic_dances():
    weights = database.get_classifier_weights()
    favorites = database.get_favorites()
    for song in database.get_all_songs():
        if song.get("dance_manual"):
            continue
        result = classify_dance(
            song.get("bpm"), song.get("time_signature"),
            favorites=favorites, weights=weights,
            **{key: song.get(key) for key in ("energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness")}
        )
        database.save_dance(
            song["isrc"],
            result["best"]["name"] if result["best"] else None,
            result["alternatives"], manual=False
        )


@app.route("/api/classifier-weights", methods=["GET", "POST"])
def classifier_weights():
    if request.method == "GET":
        return jsonify(database.get_classifier_weights())
    data = request.get_json(silent=True) or {}
    try:
        weight_keys = ("bpm","energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness")
        weights = {key: float(data.get(key)) for key in weight_keys}
        if any(value < 0 or value > 100 for value in weights.values()):
            raise ValueError("Alle Prozentwerte müssen zwischen 0 und 100 liegen.")
        if round(sum(weights.values()), 5) != 100:
            raise ValueError("Alle Gewichtungen müssen zusammen genau 100 % ergeben.")
        database.set_classifier_weights(**weights)
        refresh_all_automatic_dances()
        refresh_current_dance()
        return jsonify({"success": True, "weights": database.get_classifier_weights()})
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/database-cache", methods=["GET", "POST"])
def database_cache_api():
    if request.method == "GET":
        return jsonify({"enabled": database.get_use_database_cache()})
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    database.set_use_database_cache(enabled)
    return jsonify({"success": True, "enabled": database.get_use_database_cache(),
                    "message": "Datenbank wird vor der Analyse geprüft." if enabled else "Datenbank-Cache ist deaktiviert; Songs werden direkt beim gewählten Anbieter analysiert."})


@app.route("/api/dance-favorite", methods=["POST"])
def dance_favorite():
    data = request.get_json(silent=True) or {}
    dance = str(data.get("dance", "")).strip()
    favorite = bool(data.get("favorite"))
    if dance not in DANCE_NAMES:
        return jsonify({"error": "Unbekannter Tanz."}), 400
    database.set_favorite(dance, favorite)
    refresh_current_dance()
    return jsonify({"success": True, "favorites": database.get_favorites()})


def refresh_current_dance():
    global analysis
    with lock:
        if not analysis:
            return
        isrc = analysis.get("isrc")
        saved = database.get_song(isrc) if isrc else None
        if saved and saved.get("dance_manual"):
            return
        result = classify_dance(
            analysis.get("bpm"), analysis.get("time_signature"),
            favorites=database.get_favorites(),
            weights=database.get_classifier_weights(),
            **{key: analysis.get(key) for key in ("energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness")}
        )
        analysis["dance"] = result
        if isrc:
            database.save_dance(
                isrc,
                result["best"]["name"] if result["best"] else None,
                result["alternatives"],
                manual=False
            )


@app.route("/api/song/<isrc>/edit", methods=["POST"])
def edit_song(isrc):
    data = request.get_json(silent=True) or {}
    existing = database.get_song(isrc)
    if not existing:
        return jsonify({"error": "Song nicht in der Datenbank gefunden."}), 404

    try:
        update_tempo = "bpm" in data or "time_signature" in data
        update_dance = "dance" in data or "alternatives" in data
        bpm = float(data["bpm"]) if "bpm" in data else existing.get("bpm")
        meter = int(data["time_signature"]) if "time_signature" in data else existing.get("time_signature")

        if update_tempo and (bpm is None or bpm <= 0 or meter is None):
            raise ValueError("Bitte gültige BPM und Taktart eingeben.")
        if meter is not None and meter not in (2, 3, 4, 5, 6, 7, 8, 9, 12):
            raise ValueError("Ungültige Taktart.")

        if data.get("recalculate") and update_tempo:
            calculated = classify_dance(
                bpm, meter, favorites=database.get_favorites(),
                weights=database.get_classifier_weights(),
                **{key: existing.get(key) for key in ("energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness")}
            )
            dance = calculated["best"]["name"] if calculated["best"] else None
            alternatives = calculated["alternatives"]
            update_dance = True
            manual_dance = False
        elif update_dance:
            dance = str(data.get("dance") or "").strip() or None
            alternatives = data.get("alternatives", [])
            if dance and dance not in DANCE_NAMES:
                raise ValueError("Unbekannter Haupttanz.")
            if not isinstance(alternatives, list):
                raise ValueError("Alternativen müssen eine Liste sein.")
            alternatives = [
                {"name": str(name), "score": None, "favorite": str(name) in database.get_favorites()}
                for name in alternatives
                if str(name) in DANCE_NAMES and str(name) != dance
            ]
            manual_dance = True
        else:
            dance = existing.get("dance")
            alternatives = existing.get("dance_alternatives") or []
            manual_dance = False

        ok = database.update_song_values(
            isrc, bpm=bpm, time_signature=meter,
            dance=dance, alternatives=alternatives,
            update_tempo=update_tempo, update_dance=update_dance,
            manual_dance=manual_dance if update_dance else False
        )
        if not ok:
            return jsonify({"error": "Song nicht gefunden."}), 404

        # Wenn es der gerade laufende Song ist, aktualisiere die Anzeige sofort.
        with lock:
            if analysis and analysis.get("isrc") == isrc:
                analysis["bpm"] = bpm
                analysis["time_signature"] = meter
                if update_dance:
                    analysis["dance"] = {
                        "best": {"name": dance, "score": None} if dance else None,
                        "alternatives": alternatives,
                        "message": None if dance else "Noch kein Tanz zugeordnet.",
                        "manual": manual_dance
                    }
                elif update_tempo:
                    analysis["dance"] = classify_dance(
                        bpm, meter, favorites=database.get_favorites(),
                        weights=database.get_classifier_weights(),
                        **{key: analysis.get(key) for key in ("energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness")}
                    )
                analysis["cached"] = True

        return jsonify({"success": True, "song": database.get_song(isrc)})
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/song/manual", methods=["POST"])
def create_manual_song():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    artist = str(data.get("artist", "")).strip()
    dance = str(data.get("dance", "")).strip() or None
    try:
        bpm = float(data.get("bpm"))
        meter = int(data.get("time_signature"))
        energy = float(data["energy"]) if data.get("energy") not in (None, "") else None
        danceability = float(data["danceability"]) if data.get("danceability") not in (None, "") else None
        if not title or not artist:
            raise ValueError("Bitte Songtitel und Interpret eingeben.")
        if not 1 <= bpm <= 400:
            raise ValueError("BPM müssen zwischen 1 und 400 liegen.")
        if meter not in (2, 3, 4, 5, 6, 7, 8, 9, 12):
            raise ValueError("Ungültige Taktart.")
        for label, value in (("Energy", energy), ("Danceability", danceability)):
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{label} muss zwischen 0 und 1 liegen.")
        if dance and dance not in DANCE_NAMES:
            raise ValueError("Unbekannter Tanz.")
        local_id = database.save_manual_song(
            title, artist, bpm, meter, energy, danceability, dance
        )
        if not dance:
            result = classify_dance(
                bpm, meter, favorites=database.get_favorites(),
                weights=database.get_classifier_weights(),
                energy=energy, danceability=danceability
            )
            database.save_dance(local_id, result["best"]["name"] if result["best"] else None,
                                result["alternatives"], manual=False)
        return jsonify({"success": True, "isrc": local_id, "song": database.get_song(local_id)})
    except (TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/run-playlist", methods=["POST"])
def run_playlist():
    global playlist_status
    if not ensure_access_token():
        return jsonify({"error": "Spotify ist nicht verbunden. Bitte erneut anmelden."}), 401
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Bitte einen Spotify-Playlist-Link eingeben."}), 400
    try:
        playlist_id = extract_playlist_id(url)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    with playlist_lock:
        if playlist_status["running"]:
            return jsonify({"error": "Eine Playlist wird bereits analysiert."}), 409
        playlist_status = {
            "running": True, "total": 0, "processed": 0,
            "failed": 0, "message": "Playlist wird geladen."
        }
    threading.Thread(target=analyze_playlist_worker, args=(playlist_id,), daemon=True).start()
    return jsonify({"message": "Playlist-Analyse gestartet."})


@app.route("/api/playlist-status")
def get_playlist_status():
    with playlist_lock:
        return jsonify(playlist_status.copy())


def reanalyze_library_worker():
    """Analysiert alle externen Songs sequenziell in derselben Reihenfolge wie die Bibliothek."""
    global reanalysis_status
    try:
        songs = database.get_all_songs()
        provider = analysis_provider.get_provider()
        with reanalysis_lock:
            reanalysis_status.update(
                running=True, total=len(songs), processed=0, failed=0, skipped=0,
                message="Erneute Analyse wird vorbereitet."
            )

        for index, saved in enumerate(songs, start=1):
            title = saved.get("title") or "Unbekannter Song"
            artist = saved.get("artist") or "Unbekannter Interpret"
            isrc = str(saved.get("isrc") or "")
            try:
                # Manuell angelegte Songs haben keine Provider-ISRC.
                if saved.get("source") == "manual" or isrc.startswith("MANUAL-"):
                    with reanalysis_lock:
                        reanalysis_status["skipped"] += 1
                else:
                    track_id = saved.get("spotify_track_id") or (isrc.removeprefix("SPOTIFY-") if isrc.startswith("SPOTIFY-") else None)
                    if provider == "musicstax" and not track_id:
                        track_id = find_spotify_track_id(title, artist)
                    fresh = get_song_data_without_api(
                        title, artist, force_refresh=True, provider=provider,
                        spotify_track_id=track_id
                    )
                    if not fresh or not fresh.get("isrc"):
                        raise ValueError("Keine gültigen Analysedaten erhalten.")
                    fresh = persist_song_result(fresh, preferred_isrc=isrc)
                    # attach_dance_result schützt auch manuell festgelegte Tänze.
                    attach_dance_result(fresh)
            except Exception as error:
                print(f"Erneute Analyse fehlgeschlagen: {title} – {error}")
                with reanalysis_lock:
                    reanalysis_status["failed"] += 1
            finally:
                with reanalysis_lock:
                    reanalysis_status["processed"] += 1
                    reanalysis_status["message"] = (
                        f"{provider}: {title} ({index}/{len(songs)})"
                    )

        with reanalysis_lock:
            reanalysis_status["running"] = False
            reanalysis_status["message"] = "Erneute Analyse abgeschlossen."
    except Exception as error:
        with reanalysis_lock:
            reanalysis_status["running"] = False
            reanalysis_status["message"] = f"Fehler: {error}"


@app.route("/api/analysis-provider", methods=["GET", "POST"])
def analysis_provider_api():
    if request.method == "GET":
        selected = analysis_provider.get_provider()
        return jsonify({"provider": selected, "musicstax_available": True,
                        "message": "Musicstax ist aktiv. Der erste Abruf startet Chrome/Edge automatisch." if selected == "musicstax" else "MeloData ist aktiv."})

    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider", "")).strip().lower()
    if provider not in ("melodata", "musicstax"):
        return jsonify({"error": "Ungültige Analysequelle."}), 400
    analysis_provider.set_provider(provider)
    # Re-run the currently playing track immediately after switching source.
    with lock:
        song = current_song.copy() if current_song else None
        track_id = song.get("id") if song else None
    if song and song.get("artists") and track_id:
        threading.Thread(target=analyze_current_song, args=(song, track_id), daemon=True).start()
    return jsonify({"success": True, "provider": provider,
                    "message": "Musicstax aktiviert – der aktuelle Song wird neu analysiert." if provider == "musicstax" else "MeloData aktiviert – der aktuelle Song wird neu analysiert."})


@app.route("/api/reanalyze-library", methods=["POST"])
def start_reanalyze_library():
    with reanalysis_lock:
        if reanalysis_status["running"]:
            return jsonify({"error": "Die Bibliothek wird bereits neu analysiert."}), 409
        reanalysis_status.update(
            running=True, total=0, processed=0, failed=0, skipped=0,
            message="Erneute Analyse startet."
        )
    threading.Thread(target=reanalyze_library_worker, daemon=True).start()
    return jsonify({"success": True, "message": "Erneute Analyse gestartet."})


@app.route("/api/reanalyze-library/status")
def reanalyze_library_status():
    with reanalysis_lock:
        return jsonify(reanalysis_status.copy())


def start_server():
    database.initialize()
    saved_rules = database.get_dance_rules(DANCE_RULES)
    DANCE_RULES[:] = saved_rules
    threading.Thread(target=spotify_worker, daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000/login")).start()
    app.run(host="127.0.0.1", port=8000, debug=False, use_reloader=False, threaded=True)
