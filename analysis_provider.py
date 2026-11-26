"""Einheitlicher Einstiegspunkt für MeloData und Musicstax."""
import database
import melodata
import musicstax_browser


def get_provider():
    return database.get_setting("analysis_provider", "melodata")


def set_provider(provider):
    if provider not in ("melodata", "musicstax"):
        raise ValueError("Unbekannte Analysequelle.")
    database.set_setting("analysis_provider", provider)
    return provider


def analyze_song(title, artist, force_refresh=False, provider=None, spotify_track_id=None):
    provider = provider or get_provider()
    if provider == "melodata":
        return melodata.analyze_song(title, artist, force_refresh=force_refresh)
    if provider == "musicstax":
        if not spotify_track_id:
            raise RuntimeError("Für Musicstax wird eine Spotify-Track-ID benötigt. Starte den Song über Spotify neu oder wähle MeloData.")
        return musicstax_browser.fetch_track(spotify_track_id, title=title, artist=artist)
    raise RuntimeError(f"Unbekannte Analysequelle: {provider}")
