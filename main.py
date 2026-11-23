
import time
import spotify

spotify.start_server()

print("DanceDetector gestartet.")
print("Website: http://127.0.0.1:8000")

try:
    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\nDanceDetector beendet.")
