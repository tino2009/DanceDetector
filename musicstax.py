"""Musicstax HTML audio-feature reader.

Uses the public track page and extracts the rendered Audio features section.
No Musicstax account or API key is used. This is an unofficial website parser;
site markup / anti-bot protection can change at any time.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import requests

BASE_URL = "https://metrics.musicstax.com/track/{track_id}/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,de;q=0.8",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)


def _flat_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return " ".join(parser.parts)


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _match(pattern: str, text: str, field: str) -> str:
    found = re.search(pattern, text, flags=re.IGNORECASE)
    if not found:
        raise ValueError(
            f"Musicstax-Seite enthält das Feld '{field}' nicht im erwarteten Format. "
            "Möglicherweise hat Musicstax die Website geändert."
        )
    return found.group(1)


def parse_track_html(html: str, track_id: str, title: str | None = None,
                     artist: str | None = None) -> dict[str, Any]:
    """Parse the Audio features visible in the supplied Musicstax HTML."""
    text = _flat_text(html)
    if "Audio features" not in text:
        raise ValueError("Kein Audio-Features-Bereich in der Musicstax-Response gefunden.")

    key = _match(r"\bKey\s+(.+?)\s+Tempo\b", text, "Key")
    bpm = _number(_match(r"\bTempo\s+([0-9]+(?:[.,][0-9]+)?)\s*BPM", text, "Tempo"))
    meter_num, meter_den = re.match(
        r"(\d+)\s*/\s*(\d+)", _match(r"\bTime\s+(\d+\s*/\s*\d+)", text, "Time Signature")
    ).groups()
    meter_num, meter_den = int(meter_num), int(meter_den)
    if meter_den != 4:
        raise ValueError(f"Ungewöhnlicher Takt {meter_num}/{meter_den}; Datenbank erwartet derzeit den Zähler.")

    loudness = _number(_match(
        r"\bLoudness\s+(-?[0-9]+(?:[.,][0-9]+)?)\s*dB", text, "Loudness"
    ))

    def percent(label: str) -> float:
        raw = _match(
            rf"\b{re.escape(label)}\s+([0-9]+(?:[.,][0-9]+)?)\s*%",
            text, label,
        )
        return _number(raw) / 100.0

    return {
        # Existing DB uses the ISRC column as its primary key. This stable
        # synthetic key is explicitly NOT an actual ISRC.
        "isrc": f"SPOTIFY-{track_id}",
        "spotify_track_id": track_id,
        "title": title or "",
        "artist": artist or "",
        "bpm": bpm,
        "time_signature": meter_num,
        "time_signature_denominator": meter_den,
        "tempo_confidence": None,
        "key": key,
        "loudness": loudness,
        "energy": percent("Energy"),
        "danceability": percent("Danceability"),
        "valence": percent("Valence"),
        "speechiness": percent("Speechiness"),
        "liveness": percent("Liveness"),
        "acousticness": percent("Acousticness"),
        "instrumentalness": percent("Instrumentalness"),
        "source": "musicstax",
        "cached": False,
    }


def get_track_id(value: str) -> str:
    """Accept a Spotify ID or a Spotify/Musicstax track URL."""
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9]{22}", value):
        return value
    match = re.search(r"/track/([A-Za-z0-9]{22})/?(?:\?|$)", value)
    if match:
        return match.group(1)
    raise ValueError("Keine gültige Spotify-Track-ID bzw. Track-URL übergeben.")


def fetch_track(track_id_or_url: str, title: str | None = None,
                artist: str | None = None, timeout: int = 20) -> dict[str, Any]:
    track_id = get_track_id(track_id_or_url)
    url = BASE_URL.format(track_id=track_id)
    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"Musicstax konnte nicht erreicht werden: {exc}") from exc

    if response.status_code in (403, 429):
        raise RuntimeError(
            f"Musicstax blockiert den automatisierten Abruf (HTTP {response.status_code}). "
            "Der Browserzugriff funktioniert deshalb nicht automatisch auch aus Python."
        )
    response.raise_for_status()
    return parse_track_html(response.text, track_id, title=title, artist=artist)
