"""Heuristische Tanzzuordnung anhand von Tempo, Takt, Energy und Danceability."""

DANCE_RULES = [
    {"name": "Salsa", "meter": 4, "min": 85, "max": 125, "ideal": 105, "weight": 1.10, "energy": .78, "danceability": .78},
    {"name": "Cha-Cha-Cha", "meter": 4, "min": 90, "max": 130, "ideal": 110, "weight": 1.08, "energy": .72, "danceability": .75},
    {"name": "Discofox", "meter": 4, "min": 95, "max": 145, "ideal": 120, "weight": 1.00, "energy": .70, "danceability": .72},
    {"name": "Rumba", "meter": 4, "min": 75, "max": 115, "ideal": 95, "weight": .96, "energy": .42, "danceability": .55},
    {"name": "Tango", "meter": 4, "min": 110, "max": 140, "ideal": 125, "weight": .98, "energy": .60, "danceability": .58},
    {"name": "Samba", "meter": 4, "min": 90, "max": 120, "ideal": 105, "weight": .95, "energy": .82, "danceability": .78},
    {"name": "Jive", "meter": 4, "min": 135, "max": 190, "ideal": 165, "weight": .98, "energy": .88, "danceability": .82},
    {"name": "Quickstep", "meter": 4, "min": 175, "max": 220, "ideal": 200, "weight": .98, "energy": .90, "danceability": .80},
    {"name": "Slowfox", "meter": 4, "min": 105, "max": 135, "ideal": 120, "weight": .95, "energy": .48, "danceability": .58},
    {"name": "Langsamer Walzer", "meter": 3, "min": 80, "max": 115, "ideal": 95, "weight": 1.0, "energy": .42, "danceability": .50},
    {"name": "Wiener Walzer", "meter": 3, "min": 135, "max": 190, "ideal": 165, "weight": 1.0, "energy": .75, "danceability": .65},
]
DANCE_NAMES = [dance["name"] for dance in DANCE_RULES]


def _similarity(value, target):
    if value is None:
        return None
    try:
        return max(0.0, 1.0 - abs(float(value) - target) / 0.65)
    except (TypeError, ValueError):
        return None


def classify_dance(bpm, time_signature, favorites=None, energy=None, danceability=None):
    if bpm is None or time_signature is None:
        return {"best": None, "alternatives": [], "message": "BPM oder Takt nicht verfügbar."}
    try:
        bpm, time_signature = float(bpm), int(time_signature)
    except (TypeError, ValueError):
        return {"best": None, "alternatives": [], "message": "BPM oder Takt ist ungültig."}
    if bpm <= 0:
        return {"best": None, "alternatives": [], "message": "BPM muss größer als 0 sein."}

    favorites = set(favorites or [])
    candidates = [bpm, bpm / 2, bpm * 2]
    results = []
    for dance in DANCE_RULES:
        if dance["meter"] != time_signature:
            continue
        candidate = next((v for v in sorted(candidates, key=lambda x: abs(x-dance["ideal"])) if dance["min"] <= v <= dance["max"]), None)
        if candidate is None:
            continue
        half_range = max(dance["ideal"] - dance["min"], dance["max"] - dance["ideal"], 1)
        tempo_score = max(0.0, 1.0 - abs(candidate-dance["ideal"]) / half_range)
        components = [(0.60, tempo_score)]
        energy_sim = _similarity(energy, dance["energy"])
        dance_sim = _similarity(danceability, dance["danceability"])
        if energy_sim is not None:
            components.append((0.28, energy_sim))
        if dance_sim is not None:
            components.append((0.12, dance_sim))
        weight_sum = sum(w for w, _ in components)
        normalized = sum(w*v for w, v in components) / weight_sum
        score = min(99.0, (55 + 45 * normalized) * dance["weight"])
        results.append({"name": dance["name"], "score": round(score, 1), "matched_bpm": round(candidate, 1), "favorite": dance["name"] in favorites})

    results.sort(key=lambda item: item["score"], reverse=True)
    eligible = [item for item in results if item["favorite"] and item["score"] >= 90]
    if eligible:
        preferred = max(eligible, key=lambda item: item["score"])
        results.remove(preferred)
        results.insert(0, preferred)
    return {"best": results[0] if results else None, "alternatives": results[1:] if results else [], "message": None if results else "Kein passender Tanz gefunden."}
