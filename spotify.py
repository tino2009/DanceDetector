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
        raise ValueError("MeloData hat kein gültiges Ergebnis geliefert.")
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
            energy=result.get("energy"), danceability=result.get("danceability")
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


def analyze_current_song(song, track_id):
    global analysis, analysis_status, analysis_error
    with lock:
        analysis_status = "Analysiere Song mit MeloData..."
        analysis_error = None
        analysis = None
    try:
        result = melodata.analyze_song(song["name"], song["artists"][0])
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
        print("MeloData Fehler:", error)


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
            time.sleep(5)
        except Exception as error:
            print("Spotify Worker Fehler:", error)
            time.sleep(10)


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
                result = melodata.analyze_song(track["name"], track["artist"])
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
                energy=current_analysis.get("energy"),
                danceability=current_analysis.get("danceability")
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


@app.route("/api/dance-options")
def dance_options():
    return jsonify([
        {
            "name": dance["name"], "meter": dance["meter"],
            "min_bpm": dance["min"], "max_bpm": dance["max"],
            "ideal_bpm": dance["ideal"]
        }
        for dance in DANCE_RULES
    ])


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
            energy=analysis.get("energy"), danceability=analysis.get("danceability")
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
                energy=existing.get("energy"), danceability=existing.get("danceability")
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
                        energy=analysis.get("energy"),
                        danceability=analysis.get("danceability")
                    )
                analysis["cached"] = True

        return jsonify({"success": True, "song": database.get_song(isrc)})
    except (KeyError, TypeError, ValueError) as error:
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


def start_server():
    database.initialize()
    threading.Thread(target=spotify_worker, daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000/login")).start()
    app.run(host="127.0.0.1", port=8000, debug=False, use_reloader=False, threaded=True)
