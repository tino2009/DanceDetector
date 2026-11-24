
import os
import secrets
import threading
import time
import webbrowser

import requests

from flask import Flask, request, jsonify, redirect, render_template
from dotenv import load_dotenv

import melodata
import database

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
    raise RuntimeError("Spotify-Zugangsdaten fehlen.")

SCOPE = "user-read-currently-playing"

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

lock = threading.Lock()


def spotify_headers():
    return {
        "Authorization": f"Bearer {access_token}"
    }


def refresh_access_token():
    global access_token, token_expires_at, refresh_token

    if not refresh_token:
        return False

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=15
    )

    if response.status_code != 200:
        print("Spotify Token Refresh fehlgeschlagen:", response.text)
        return False

    data = response.json()

    access_token = data["access_token"]
    token_expires_at = time.time() + data.get("expires_in", 3600)

    refresh_token = data.get("refresh_token", refresh_token)

    return True


def get_current_song():
    global access_token

    if not access_token:
        return None

    if time.time() >= token_expires_at - 60:
        if not refresh_access_token():
            return None

    response = requests.get(
        "https://api.spotify.com/v1/me/player/currently-playing",
        headers=spotify_headers(),
        timeout=15
    )

    if response.status_code == 401:
        if not refresh_access_token():
            return None

        response = requests.get(
            "https://api.spotify.com/v1/me/player/currently-playing",
            headers=spotify_headers(),
            timeout=15
        )

    if response.status_code == 204:
        return None

    if response.status_code != 200:
        print("Spotify Fehler:", response.status_code)
        return None

    data = response.json()
    track = data.get("item")

    if not track:
        return None

    return {
        "id": track["id"],
        "name": track["name"],
        "artists": [
            artist["name"] for artist in track["artists"]
        ],
        "is_playing": data["is_playing"]
    }


def analyze_current_song(song):
    global analysis, analysis_status, analysis_error

    with lock:
        analysis_status = "Analysiere Song mit MeloData..."
        analysis_error = None
        analysis = None

    try:
        result = melodata.analyze_song(
            song["name"],
            song["artists"][0]
        )

        with lock:
            analysis = result
            analysis_status = (
                "Aus lokaler Datenbank"
                if result.get("cached")
                else "Analyse abgeschlossen"
            )

    except Exception as error:
        with lock:
            analysis_error = str(error)
            analysis_status = "Analyse fehlgeschlagen"

        print("MeloData Fehler:", error)


def spotify_worker():
    global current_song, last_track_id

    while True:
        try:
            song = get_current_song()

            if song:
                current_song = song

                if song["id"] != last_track_id:
                    last_track_id = song["id"]

                    threading.Thread(
                        target=analyze_current_song,
                        args=(song,),
                        daemon=True
                    ).start()

            else:
                current_song = None

            time.sleep(5)

        except Exception as error:
            print("Spotify Worker Fehler:", error)
            time.sleep(10)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    url = "https://accounts.spotify.com/authorize"

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state
    }

    auth_url = requests.Request(
        "GET", url, params=params
    ).prepare().url

    return redirect(auth_url)


@app.route("/api/spotify/callback")
def callback():
    global access_token, refresh_token, token_expires_at

    code = request.args.get("code")

    if request.args.get("state") != state:
        return "Spotify State ungültig.", 403

    if not code:
        return "Kein Authorization Code erhalten.", 400

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=15
    )

    if response.status_code != 200:
        return f"Spotify Fehler: {response.text}", 400

    data = response.json()

    access_token = data["access_token"]
    refresh_token = data.get("refresh_token")
    token_expires_at = time.time() + data.get("expires_in", 3600)

    print("Spotify erfolgreich verbunden!")

    return redirect("/")


@app.route("/api/state")
def api_state():
    with lock:
        return jsonify({
            "connected": access_token is not None,
            "song": current_song,
            "analysis": analysis,
            "analysis_status": analysis_status,
            "analysis_error": analysis_error,
            "usage": database.get_usage(),
            "song_count": database.get_song_count()
        })


@app.route("/api/songs")
def api_songs():
    return jsonify(database.get_all_songs())


def start_server():
    threading.Thread(
        target=spotify_worker,
        daemon=True
    ).start()

    threading.Timer(
        1.5,
        lambda: webbrowser.open("http://127.0.0.1:8000/login")
    ).start()

    app.run(
        host="127.0.0.1",
        port=8000,
        debug=False,
        use_reloader=False,
        threaded=True
    )
