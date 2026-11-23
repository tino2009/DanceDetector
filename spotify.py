
import os
import secrets
import threading
import time
import webbrowser

import requests
from flask import Flask, request, jsonify, redirect
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")

if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
    raise RuntimeError("Spotify-Zugangsdaten fehlen in der .env-Datei")

SCOPE = "user-read-currently-playing"

app = Flask(__name__)

state = secrets.token_urlsafe(16)
access_token = None


# -------------------------
# WEBSITE
# -------------------------

@app.route("/")
def index():
    return """
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <title>DanceDetector</title>

        <style>
            * {
                box-sizing: border-box;
            }

            body {
                margin: 0;
                background: #0f1117;
                color: white;
                font-family: Arial, sans-serif;
                min-height: 100vh;

                display: flex;
                justify-content: center;
                align-items: center;
            }

            .container {
                width: 90%;
                max-width: 600px;
                text-align: center;
            }

            .logo {
                color: #1DB954;
                font-size: 14px;
                font-weight: bold;
                letter-spacing: 4px;
                margin-bottom: 30px;
            }

            .card {
                background: #1b1e27;
                padding: 50px 30px;
                border-radius: 20px;
                border: 1px solid #292d38;
            }

            .label {
                color: #8b919e;
                font-size: 12px;
                letter-spacing: 3px;
                margin-bottom: 20px;
            }

            #song-title {
                font-size: 36px;
                font-weight: bold;
                margin-bottom: 12px;
                word-break: break-word;
            }

            #artist {
                font-size: 20px;
                color: #a7acb7;
            }

            .status {
                margin-top: 35px;
                color: #1DB954;
                font-size: 13px;
            }

            .dot {
                display: inline-block;
                width: 8px;
                height: 8px;
                background: #1DB954;
                border-radius: 50%;
                margin-right: 8px;
            }

            .footer {
                color: #666c78;
                margin-top: 25px;
                font-size: 12px;
            }

            .empty {
                color: #9ca3af;
            }
        </style>
    </head>

    <body>

        <div class="container">

            <div class="logo">DANCE DETECTOR</div>

            <div class="card">

                <div class="label">NOW PLAYING</div>

                <div id="song-title">Lade Song...</div>

                <div id="artist"></div>

                <div class="status" id="status">
                    <span class="dot"></span>
                    Verbinde mit Spotify
                </div>

            </div>

            <div class="footer">
                Live Spotify Status
            </div>

        </div>

        <script>
            async function updateSong() {
                try {
                    const response = await fetch("/api/current-song");
                    const data = await response.json();

                    const title = document.getElementById("song-title");
                    const artist = document.getElementById("artist");
                    const status = document.getElementById("status");

                    if (data.connected === false) {
                        title.textContent = "Spotify nicht verbunden";
                        artist.textContent = "";
                        status.textContent = "Bitte Spotify verbinden";
                        return;
                    }

                    if (!data.song) {
                        title.textContent = "Kein Song";
                        artist.textContent = "Spotify spielt gerade nichts ab";
                        status.textContent = "";
                        return;
                    }

                    title.textContent = data.song.name;
                    artist.textContent = data.song.artists.join(", ");

                    if (data.song.is_playing) {
                        status.innerHTML =
                            '<span class="dot"></span> NOW PLAYING';
                    } else {
                        status.textContent = "PAUSED";
                    }

                } catch (error) {
                    document.getElementById("song-title").textContent =
                        "Verbindung fehlgeschlagen";

                    document.getElementById("artist").textContent =
                        "Backend nicht erreichbar";
                }
            }

            updateSong();

            setInterval(updateSong, 3000);
        </script>

    </body>
    </html>
    """


# -------------------------
# SPOTIFY LOGIN
# -------------------------

def login() -> str:
    url = "https://accounts.spotify.com/authorize"

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state
    }

    auth_url = requests.Request(
        "GET",
        url,
        params=params
    ).prepare().url

    if auth_url is None:
        raise RuntimeError("Spotify Login-URL konnte nicht erstellt werden")

    return auth_url


@app.route("/api/spotify/callback")
def callback():
    global access_token

    code = request.args.get("code")
    returned_state = request.args.get("state")

    if returned_state != state:
        return "State stimmt nicht überein!", 403

    if not code:
        return "Kein Authorization Code erhalten!", 400

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI
        },
        auth=(CLIENT_ID, CLIENT_SECRET)
    )

    if response.status_code != 200:
        return f"Spotify Fehler: {response.text}", response.status_code

    access_token = response.json()["access_token"]

    print("Spotify erfolgreich verbunden!")

    return redirect("/")


# -------------------------
# SPOTIFY API
# -------------------------

def get_current_song():

    if access_token is None:
        return None

    response = requests.get(
        "https://api.spotify.com/v1/me/player/currently-playing",
        headers={
            "Authorization": f"Bearer {access_token}"
        },
        timeout=10
    )

    if response.status_code == 204:
        return None

    if response.status_code != 200:
        print("Spotify API Fehler:", response.status_code)
        return None

    data = response.json()
    track = data.get("item")

    if track is None:
        return None

    return {
        "name": track["name"],
        "artists": [
            artist["name"]
            for artist in track["artists"]
        ],
        "is_playing": data["is_playing"]
    }


# -------------------------
# WEBSITE API
# -------------------------

@app.route("/api/current-song")
def current_song_api():

    if access_token is None:
        return jsonify({
            "connected": False,
            "song": None
        })

    song = get_current_song()

    return jsonify({
        "connected": True,
        "song": song
    })


# -------------------------
# START
# -------------------------

def start_server():

    server_thread = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1",
            port=8000,
            debug=False,
            use_reloader=False,
            threaded=True
        ),
        daemon=True
    )

    server_thread.start()

    time.sleep(1)

    webbrowser.open(login())
