"""Dance classification using tempo and available Musicstax audio features."""
DANCE_RULES = [
{"name":"Salsa","meter":4,"min":85,"max":125,"ideal":105,"weight":1.10,"energy":.78,"danceability":.82,"valence":.78,"acousticness":.30,"speechiness":.05,"instrumentalness":.35,"liveness":.22,"loudness":-8},
{"name":"Cha-Cha-Cha","meter":4,"min":90,"max":130,"ideal":110,"weight":1.08,"energy":.72,"danceability":.78,"valence":.75,"acousticness":.35,"speechiness":.05,"instrumentalness":.40,"liveness":.18,"loudness":-9},
{"name":"Discofox","meter":4,"min":95,"max":145,"ideal":120,"weight":1.00,"energy":.75,"danceability":.78,"valence":.72,"acousticness":.25,"speechiness":.04,"instrumentalness":.25,"liveness":.12,"loudness":-7},
{"name":"Rumba","meter":4,"min":75,"max":115,"ideal":95,"weight":.96,"energy":.42,"danceability":.48,"valence":.55,"acousticness":.55,"speechiness":.04,"instrumentalness":.35,"liveness":.12,"loudness":-11},
{"name":"Tango","meter":4,"min":110,"max":140,"ideal":125,"weight":.98,"energy":.60,"danceability":.62,"valence":.48,"acousticness":.45,"speechiness":.04,"instrumentalness":.55,"liveness":.15,"loudness":-9},
{"name":"Samba","meter":4,"min":90,"max":120,"ideal":105,"weight":.95,"energy":.82,"danceability":.80,"valence":.78,"acousticness":.35,"speechiness":.04,"instrumentalness":.40,"liveness":.20,"loudness":-8},
{"name":"Jive","meter":4,"min":135,"max":190,"ideal":165,"weight":.98,"energy":.88,"danceability":.82,"valence":.82,"acousticness":.30,"speechiness":.04,"instrumentalness":.35,"liveness":.18,"loudness":-7},
{"name":"Quickstep","meter":4,"min":175,"max":220,"ideal":200,"weight":.98,"energy":.90,"danceability":.82,"valence":.78,"acousticness":.45,"speechiness":.04,"instrumentalness":.65,"liveness":.15,"loudness":-8},
{"name":"Slowfox","meter":4,"min":105,"max":135,"ideal":120,"weight":.95,"energy":.48,"danceability":.60,"valence":.55,"acousticness":.50,"speechiness":.04,"instrumentalness":.50,"liveness":.12,"loudness":-11},
{"name":"Langsamer Walzer","meter":3,"min":80,"max":115,"ideal":95,"weight":1.0,"energy":.42,"danceability":.50,"valence":.55,"acousticness":.55,"speechiness":.04,"instrumentalness":.55,"liveness":.12,"loudness":-12},
{"name":"Wiener Walzer","meter":3,"min":135,"max":190,"ideal":165,"weight":1.0,"energy":.75,"danceability":.68,"valence":.72,"acousticness":.45,"speechiness":.04,"instrumentalness":.50,"liveness":.15,"loudness":-9},
{"name":"Bachata","meter":4,"min":105,"max":145,"ideal":125,"weight":1.02,"energy":.58,"danceability":.75,"valence":.68,"acousticness":.48,"speechiness":.05,"instrumentalness":.25,"liveness":.15,"loudness":-10},
]
DANCE_NAMES=[d["name"] for d in DANCE_RULES]
FEATURES=("energy","danceability","valence","acousticness","speechiness","instrumentalness","liveness","loudness",
"onset_strength","onset_density","syncopation","beat_strength","beat_regularity","swing_ratio",
"rhythmic_complexity","spectral_centroid","spectral_bandwidth","spectral_rolloff","spectral_flux",
"low_energy","mid_energy","high_energy","harmonic_percussive_ratio","mfcc_1","mfcc_2","mfcc_3","mfcc_4","mfcc_5","accent_1","accent_2","accent_3","accent_4")
DEFAULT_WEIGHTS={"bpm":55,"energy":10,"danceability":10,"valence":5,"acousticness":5,"speechiness":3,"instrumentalness":5,"liveness":2,"loudness":5,
"onset_strength":4,"onset_density":4,"syncopation":5,"beat_strength":3,"beat_regularity":3,"swing_ratio":3,"rhythmic_complexity":4,
"spectral_centroid":1,"spectral_bandwidth":1,"spectral_rolloff":1,"spectral_flux":2,"low_energy":2,"mid_energy":1,"high_energy":2,"harmonic_percussive_ratio":3,"mfcc_1":1,"mfcc_2":1,"mfcc_3":1,"mfcc_4":1,"mfcc_5":1,"accent_1":2,"accent_2":2,"accent_3":2,"accent_4":2}
def _similarity(value,target,scale=.65):
    if value is None:return None
    try:return max(0.0,1.0-abs(float(value)-target)/scale)
    except (TypeError,ValueError):return None
def _profile_target(name, feature):
    # Heuristic dance signatures; normalized targets are starting profiles, not trained measurements.
    latin = {"Salsa":(.82,.78,.78,.62),"Bachata":(.58,.62,.45,.55),"Cha-Cha-Cha":(.72,.72,.65,.48),
             "Rumba":(.40,.45,.35,.35),"Samba":(.85,.82,.72,.70)}
    swing = {"Jive":(.90,.82,.78,.82),"Quickstep":(.88,.78,.72,.85),"Slowfox":(.42,.55,.38,.80)}
    vals = latin.get(name, swing.get(name, (.55,.55,.50,.50)))
    mapping={"onset_strength":vals[0],"onset_density":vals[1],"syncopation":vals[2],
             "beat_strength":vals[0],"beat_regularity":.82 if name in ("Wiener Walzer","Langsamer Walzer","Quickstep") else .68,
             "swing_ratio":vals[3],"rhythmic_complexity":vals[2],
             "spectral_centroid":.58 if name in ("Jive","Samba","Salsa") else .42,
             "spectral_bandwidth":.55,"spectral_rolloff":.58,"spectral_flux":vals[1],
             "low_energy":.48,"mid_energy":.32,"high_energy":.20,
             "harmonic_percussive_ratio":.38 if name in ("Salsa","Samba","Jive") else .62,
             "mfcc_1":.5,"mfcc_2":.5,"mfcc_3":.5,"mfcc_4":.5,"mfcc_5":.5,
             "accent_1":.75,"accent_2":.45,"accent_3":.40,"accent_4":.42}
    return mapping.get(feature, .5)

def classify_dance(bpm,time_signature,favorites=None,weights=None,**features):
    if bpm is None or time_signature is None:return {"best":None,"alternatives":[],"message":"BPM oder Takt nicht verfügbar."}
    try:bpm,time_signature=float(bpm),int(time_signature)
    except (TypeError,ValueError):return {"best":None,"alternatives":[],"message":"BPM oder Takt ist ungültig."}
    if bpm<=0:return {"best":None,"alternatives":[],"message":"BPM muss größer als 0 sein."}
    favorites=set(favorites or []); weights={**DEFAULT_WEIGHTS,**(weights or {})}
    results=[]
    for dance in DANCE_RULES:
        if dance["meter"]!=time_signature:continue
        candidates=[bpm,bpm/2,bpm*2]
        candidate=next((v for v in sorted(candidates,key=lambda x:abs(x-dance["ideal"])) if dance["min"]<=v<=dance["max"]),None)
        if candidate is None:continue
        span=max(dance["ideal"]-dance["min"],dance["max"]-dance["ideal"],1)
        components=[(float(weights.get("bpm",0)),max(0.0,1-abs(candidate-dance["ideal"])/span))]
        for feature in FEATURES:
            value=features.get(feature)
            if value is None:continue
            scale=25.0 if feature=="loudness" else (.20 if feature=="speechiness" else (.35 if feature in ("onset_density","syncopation","swing_ratio","rhythmic_complexity") else .65))
            target = dance.get(feature)
            if target is None:
                target = _profile_target(dance["name"], feature)
            components.append((float(weights.get(feature,0)),_similarity(value,target,scale)))
        total=sum(w for w,_ in components if w>0)
        normalized=sum(w*v for w,v in components if w>0)/total if total else 0
        score=min(99.0,(55+45*normalized)*dance["weight"])
        results.append({"name":dance["name"],"score":round(score,1),"matched_bpm":round(candidate,1),"favorite":dance["name"] in favorites})
    results.sort(key=lambda x:x["score"],reverse=True)
    preferred=[x for x in results if x["favorite"] and x["score"]>=90]
    if preferred:
        item=max(preferred,key=lambda x:x["score"]);results.remove(item);results.insert(0,item)
    return {"best":results[0] if results else None,"alternatives":results[1:],"message":None if results else "Kein passender Tanz gefunden."}
