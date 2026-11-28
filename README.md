# DanceDetector – Installation und Audioanalyse (Windows)

## 1. Voraussetzungen

- Windows 10/11
- Python 3.10–3.12 (64-Bit empfohlen)
- Spotify-Zugangsdaten und MeloData-API-Key in einer `.env`-Datei
- Ein funktionierender Windows-Audioausgang (Lautsprecher, Kopfhörer oder HDMI)

## 2. Installation

Öffne PowerShell im Projektordner und führe diese Befehle aus:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

**Wichtig:** Verwende `py -m pip` statt `pip`. Dadurch wird pip als Python-Modul gestartet und nicht direkt über `pip.exe`.

Falls Windows weiterhin meldet, dass eine Anwendungssteuerungsrichtlinie `pip.exe` oder Python blockiert, ist das keine fehlende Python-Bibliothek. Dann muss die zuständige Windows-/Schul-/Firmen-IT die Ausführung der benötigten Python-Pakete erlauben. Die Richtlinie sollte nicht umgangen werden.

Danach `.env` im Projektordner anlegen bzw. aus deiner bisherigen Installation kopieren:

```env
SPOTIFY_CLIENT_ID=deine_client_id
SPOTIFY_CLIENT_SECRET=dein_client_secret
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8000/api/spotify/callback
MELODATA_API_KEY=dein_melodata_key
```

Anschließend starten:

```powershell
py main.py
```

Die lokale Website über die im Terminal angezeigte Adresse öffnen (üblicherweise `http://127.0.0.1:8000`). **Nicht** die HTML-Datei direkt per Doppelklick öffnen. `index.html` liegt bereits unter `templates/index.html`.

## 3. Welche Pakete werden für die Audioaufnahme benötigt?

Die Audioaufnahme verwendet:

- `soundcard`: Ermittelt Windows-Audioausgänge und greift per WASAPI-Loopback auf den ausgewählten Lautsprecher-/Kopfhörerausgang zu. Es wird kein Mikrofon als Aufnahmequelle verwendet.
- `numpy`: Berechnet Signal- und Spektralmerkmale.
- `scipy`: Unterstützt die MFCC-Berechnung.

Diese Pakete werden gemeinsam mit `py -m pip install -r requirements.txt` installiert. Für die normale WASAPI-Loopback-Aufnahme ist kein separates Mikrofon und üblicherweise auch kein zusätzlicher Audiotreiber erforderlich. Der Windows-Audiotreiber muss Loopback unterstützen.

## 4. Audioanalyse verwenden

1. DanceDetector starten und die lokale Website öffnen.
2. Unter „Live-Audioanalyse“ den gewünschten Windows-Ausgang auswählen.
3. „Audioanalyse aktivieren“ einschalten.
4. Musik über genau diesen Ausgang abspielen.
5. Beim Wechsel des Spotify-Songs verwirft DanceDetector die zuvor erfassten Live-Messwerte und beginnt für den neuen Song mit einem frischen Analysefenster.
6. Zum Beenden den Toggle wieder ausschalten.

Die vier Gewichtungsregler sind:

- **Beats per Minute:** Tempo und Nähe zum typischen Tempo des Tanzes.
- **Energy:** Energie des Songs.
- **Danceability:** Tanzbarkeit des Songs.
- **Rhythmus & Klang:** Fasst intern berechnete Rhythmus- und Spektralmerkmale zusammen (z. B. Anschläge, Regelmäßigkeit, Swing und Klangverteilung). Die technischen Einzelwerte werden nicht mehr alle als separate Regler angezeigt.

Die vier Gewichte werden beim Speichern auf zusammen 100 % normiert. Intern bleiben die einzelnen Rhythmus-/Klangmerkmale erhalten und werden dem gemeinsamen Regler zugeordnet.

## 5. Fehlerbehebung

### „Failed to fetch“ im Browser

Das bedeutet normalerweise, dass der Browser den Flask-Endpunkt nicht erreichen kann – nicht, dass ein bestimmtes Audiopaket fehlt. Prüfe:

1. Läuft `py main.py` noch und zeigt das Terminal keinen Traceback?
2. Ist die Website über `http://127.0.0.1:8000` geöffnet und nicht als lokale HTML-Datei?
3. Ist die Adresse/der Port derselbe wie im Flask-Terminal?
4. Wird der Server von Firewall oder Sicherheitssoftware blockiert?

### Keine Geräte in der Auswahlliste

1. Prüfe in Windows unter **Einstellungen → System → Sound**, ob ein Ausgabegerät vorhanden und aktiv ist.
2. Spiele testweise Audio über dieses Gerät ab.
3. Starte DanceDetector nach dem Verbinden/Wechseln des Geräts neu und lade die Website neu.
4. Prüfe im Terminal, ob `soundcard` installiert wurde:

```powershell
py -m pip show soundcard
py -m pip show numpy
py -m pip show scipy
```

5. Falls die Geräteliste weiterhin leer ist, aktualisiere den Windows-Audiotreiber. Manche virtuellen Audiogeräte oder herstellerspezifischen Treiber stellen Loopback nicht bereit.

### Audioanalyse aktiv, aber kein Signal

Stelle sicher, dass Musik tatsächlich über den ausgewählten Ausgang abgespielt wird. Ein Mikrofon wird absichtlich nicht verwendet.

## 6. Technische Hinweise

- BPM, Taktart, Energy und Danceability kommen weiterhin aus MeloData/Musicstax, sofern verfügbar.
- Zusätzliche Rhythmus- und Spektralwerte werden lokal aus dem Loopback-Audiosignal berechnet.
- Swing, Synkopierung und einige Rhythmuswerte sind heuristische Schätzungen und keine trainierten, musikologisch validierten Messungen.
- Die lokale Analyse arbeitet mit kurzen Zeitfenstern. Bei einem neuen Spotify-Track werden die bisherigen Live-Features zurückgesetzt.
- `.env`, `.venv/`, `.musicstax-browser-profile/` und `dance_data.db` gehören nicht in ein öffentliches Git-Repository.

## 7. Tests

```powershell
py -m pytest -q
```


## 8. Klassifikation und Walzer

Die Klassifikation verwendet vier Gewichtungsgruppen: BPM, Energy, Danceability und Rhythmus. Die vielen technischen Live-Merkmale werden intern zusammengefasst; einzelne MFCC-Koeffizienten oder Taktakzente werden nicht als eigenständige Tanzentscheidung behandelt.

**Langsamer und Wiener Walzer sind eine Ausnahme:** Hier fließen ausschließlich Taktart (3/4) und BPM in die Bewertung ein. Energy, Danceability und Live-Rhythmuswerte verändern das Walzer-Ergebnis nicht.

Die Tanzprofile sind heuristische Startwerte und keine trainierten Modelle. Besonders bei ähnlichen Tänzen (z. B. Discofox/Bachata oder Tango/Bachata) ist BPM allein nicht immer ausreichend. Für eine zuverlässigere Unterscheidung sollten Energy und Danceability aus der Songanalyse verfügbar sein; die Live-Rhythmuswerte haben bewusst weniger Einfluss, weil einige davon nur Näherungen sind.
