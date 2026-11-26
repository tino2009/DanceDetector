"""Musicstax reader using a real local Chrome/Edge browser via Playwright CDP."""
from __future__ import annotations
import os, re, shutil, subprocess, time
from pathlib import Path
from typing import Any
from playwright.sync_api import sync_playwright

PORT = 9222
PROFILE = Path(__file__).resolve().parent / ".musicstax-browser-profile"
_process = None


def _browser_executable():
    candidates = [
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Weder Google Chrome noch Microsoft Edge wurde gefunden.")


def _start_browser():
    global _process
    executable = _browser_executable()
    PROFILE.mkdir(parents=True, exist_ok=True)
    _process = subprocess.Popen([
        executable, f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check",
        "--disable-background-networking", "about:blank"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Wait for Chromium's remote debugging endpoint to become available.
    import requests
    for _ in range(40):
        try:
            response = requests.get(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
            if response.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(.25)
    raise RuntimeError("Chrome/Edge konnte nicht für Musicstax gestartet werden.")


def _ensure_browser():
    import requests
    try:
        if requests.get(f"http://127.0.0.1:{PORT}/json/version", timeout=1).ok:
            return
    except requests.RequestException:
        pass
    _start_browser()


def _extract(text: str, pattern: str, label: str, cast=float):
    match = re.search(pattern, text, re.I)
    if not match:
        raise ValueError(f"Musicstax: {label} wurde auf der Seite nicht gefunden.")
    return cast(match.group(1).replace(',', '.'))


def parse_features(text: str) -> dict[str, Any]:
    if "AUDIO FEATURES" not in text.upper():
        raise ValueError("Musicstax hat keinen Audio-Features-Bereich angezeigt.")
    return {
        "bpm": _extract(text, r"TEMPO\s+([0-9]+(?:[.,][0-9]+)?)\s*BPM", "BPM", int),
        "time_signature": _extract(text, r"TIME\s+(\d+)\s*/\s*\d+", "Time Signature", int),
        "danceability": _extract(text, r"Danceability\s+([0-9]+(?:[.,][0-9]+)?)\s*%", "Danceability") / 100,
        "energy": _extract(text, r"Energy\s+([0-9]+(?:[.,][0-9]+)?)\s*%", "Energy") / 100,
        "liveness": _extract(text, r"Liveness\s+([0-9]+(?:[.,][0-9]+)?)\s*%", "Liveness") / 100,
        "valence": _extract(text, r"Valence\s+([0-9]+(?:[.,][0-9]+)?)\s*%", "Valence") / 100,
    }


def fetch_track(track_id: str, title: str = "", artist: str = "") -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9]{22}", str(track_id or "")):
        raise ValueError("Keine gültige Spotify-Track-ID für Musicstax vorhanden.")
    _ensure_browser()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        context = browser.contexts[0]
        page = context.new_page()
        try:
            page.goto(f"https://metrics.musicstax.com/track/{track_id}/", wait_until="domcontentloaded", timeout=60000)
            page.get_by_text(re.compile("AUDIO FEATURES", re.I)).first.wait_for(timeout=30000)
            # Wait for the rendered feature values, not an arbitrary short sleep.
            page.wait_for_function(
                r"""() => {
                    const text = document.body.innerText;
                    return /TEMPO\s+\d+\s*BPM/i.test(text)
                        && /TIME\s+\d+\s*\/\s*\d+/i.test(text)
                        && /Danceability\s+\d+(?:[.,]\d+)?\s*%/i.test(text)
                        && /Energy\s+\d+(?:[.,]\d+)?\s*%/i.test(text)
                        && /Liveness\s+\d+(?:[.,]\d+)?\s*%/i.test(text)
                        && /Valence\s+\d+(?:[.,]\d+)?\s*%/i.test(text);
                }""",
                timeout=90000
            )
            features = parse_features(page.locator("body").inner_text())
        finally:
            # Only close the tab; do not terminate the user's Musicstax browser.
            page.close()
    return {
        "isrc": f"SPOTIFY-{track_id}", "spotify_track_id": track_id,
        "title": title, "artist": artist, **features,
        "tempo_confidence": None, "source": "musicstax", "cached": False,
    }
