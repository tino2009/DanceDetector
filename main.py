
import database
import spotify

if __name__ == "__main__":
    database.initialize()

    print("================================")
    print("       DANCE DETECTOR")
    print("================================")
    print("Datenbank initialisiert.")
    print("Website: http://127.0.0.1:8000")
    print("Spotify wird gestartet...")

    try:
        spotify.start_server()

    except KeyboardInterrupt:
        print("\nDanceDetector beendet.")
