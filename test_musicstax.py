"""One-off live test: py test_musicstax.py [Spotify-Track-ID-or-URL]"""
import json
import sys

from musicstax import fetch_track

TRACK = sys.argv[1] if len(sys.argv) > 1 else "605G7F1FEjm60IdXmLhlNN"

try:
    data = fetch_track(TRACK, title="Walzer", artist="Provinz")
except Exception as exc:
    print(f"TEST FEHLGESCHLAGEN: {exc}")
    raise SystemExit(1)

print("MUSICSTAX-ABRUF ERFOLGREICH")
print(json.dumps(data, ensure_ascii=False, indent=2))
print("\nPrüfung:")
print(f"BPM: {data['bpm']}")
print(f"Takt: {data['time_signature']}/{data['time_signature_denominator']}")
print(f"Energy: {data['energy']:.2f}")
