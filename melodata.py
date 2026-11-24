
import os
import time
import requests

from dotenv import load_dotenv

import database

load_dotenv()

API_KEY = os.getenv("MELODATA_API_KEY")

BASE_URL = "https://melodata.voltenworks.com/api/v1"

if not API_KEY:
    raise RuntimeError("MELODATA_API_KEY fehlt in der .env-Datei")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}"
}


def update_quota(response):
    """Übernimmt den tatsächlichen Verbrauch von MeloData."""

    used = response.headers.get("X-Quota-Used")
    limit = response.headers.get("X-Quota-Limit")
    reset = response.headers.get("X-Quota-Reset")

    try:
        data = response.json()
        quota = data.get("meta", {}).get("quota", {})

        used = used or quota.get("used")
        limit = limit or quota.get("limit")
        reset = reset or quota.get("resets_at")

        if used is not None and limit is not None:
            database.update_usage(
                int(used),
                int(limit),
                str(reset) if reset else None
            )
    except (ValueError, TypeError):
        pass


def resolve_song(title, artist):
    """Ermittelt die ISRC anhand von Titel und Interpret."""

    response = requests.get(
        f"{BASE_URL}/tracks/resolve",
        params={
            "title": title,
            "artist": artist
        },
        headers=HEADERS,
        timeout=20
    )

    if response.status_code == 404:
        raise ValueError("Song konnte nicht gefunden werden.")

    response.raise_for_status()

    result = response.json()
    match = result.get("data", {})

    isrc = match.get("isrc")

    if not isrc:
        raise ValueError("Keine ISRC für diesen Song gefunden.")

    confidence = match.get("confidence", 0)

    if confidence < 0.70:
        raise ValueError(
            f"Songzuordnung nicht sicher genug: {confidence:.0%}"
        )

    return isrc


def get_features(isrc):
    """Fordert BPM und Takt an und behandelt HTTP 202."""

    for attempt in range(4):

        response = requests.get(
            f"{BASE_URL}/tracks/{isrc}/features",
            headers=HEADERS,
            timeout=30
        )

        update_quota(response)

        if response.status_code == 202:
            if attempt == 3:
                raise RuntimeError(
                    "MeloData analysiert den Song noch. "
                    "Bitte später erneut versuchen."
                )

            try:
                delay = float(
                    response.headers.get("Retry-After", 10)
                )
            except ValueError:
                delay = 10

            if not 0 <= delay <= 120:
                raise RuntimeError("Ungültige Retry-Angabe.")

            time.sleep(delay)
            continue

        if response.status_code == 429:
            raise RuntimeError(
                "MeloData-Limit erreicht oder zu viele Anfragen."
            )

        if response.status_code == 401:
            raise RuntimeError("MeloData API-Key ungültig.")

        response.raise_for_status()

        result = response.json()
        data = result.get("data", {})

        if data.get("status") == "unavailable":
            raise ValueError(
                "Für diesen Song sind keine Audiodaten verfügbar."
            )

        features = data.get("features", {})

        return {
            "isrc": isrc,
            "title": data.get("title"),
            "artist": data.get("artist"),
            "bpm": features.get("bpm"),
            "time_signature": features.get("time_signature"),
            "tempo_confidence": features.get("tempo_confidence"),
            "source": data.get("source")
        }

    raise RuntimeError("Analyse fehlgeschlagen.")


def analyze_song(title, artist):
    """Gesamter Analyseprozess."""

    isrc = resolve_song(title, artist)

    # Bereits analysierte Songs nicht erneut abrechnen.
    cached = database.get_song(isrc)

    if cached and cached.get("bpm") is not None:
        return {
            **cached,
            "cached": True
        }

    song = get_features(isrc)

    if song["bpm"] is None or song["time_signature"] is None:
        raise ValueError(
            "BPM oder Takt sind noch nicht verfügbar. "
            "Der Song wurde nicht vollständig gespeichert."
        )

    song["title"] = song["title"] or title
    song["artist"] = song["artist"] or artist

    database.save_song(song)

    return {
        **song,
        "cached": False
    }
