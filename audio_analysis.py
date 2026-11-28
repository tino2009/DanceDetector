
"""Windows system-output loopback capture and lightweight audio feature extraction."""
import threading, time
import numpy as np

FEATURE_NAMES = {
 "onset_strength":"Onset-Stärke", "onset_density":"Onset-Dichte",
 "syncopation":"Synkopierung", "beat_strength":"Beat-Stärke",
 "beat_regularity":"Beat-Regularität", "swing_ratio":"Swing-Verhältnis",
 "rhythmic_complexity":"Rhythmische Komplexität", "spectral_centroid":"Spektraler Schwerpunkt",
 "spectral_bandwidth":"Spektrale Bandbreite", "spectral_rolloff":"Spektraler Rolloff",
 "spectral_flux":"Spektraler Flux", "low_energy":"Tieffrequenzenergie",
 "mid_energy":"Mittenenergie", "high_energy":"Hochfrequenzenergie",
 "harmonic_percussive_ratio":"Harmonisch-perkussives Verhältnis", "mfcc_1":"MFCC 1", "mfcc_2":"MFCC 2", "mfcc_3":"MFCC 3", "mfcc_4":"MFCC 4", "mfcc_5":"MFCC 5", "accent_1":"Taktakzent 1", "accent_2":"Taktakzent 2", "accent_3":"Taktakzent 3", "accent_4":"Taktakzent 4",
}
try:
 import soundcard as sc
except Exception:
 sc = None

_lock = threading.RLock()
_state = {"running":False,"signal":False,"device":None,"features":{},"phase":"idle","seconds":0,"error":None}
_thread = None
_stop = threading.Event()

def list_devices():
 if sc is None:
  return {"available":False,"devices":[],"error":"Das Paket 'soundcard' fehlt. Installiere die Abhängigkeiten aus requirements.txt."}
 try:
  speakers=list(sc.all_speakers())
  default=sc.default_speaker()
  # Some Windows drivers expose the default output separately from all_speakers().
  if default is not None and all(x.name != default.name for x in speakers):
   speakers.insert(0, default)
  devices=[]
  seen=set()
  for speaker in speakers:
   if speaker.name not in seen:
    seen.add(speaker.name)
    devices.append({"id":speaker.name,"name":speaker.name + (" (Standard)" if default is not None and speaker.name==default.name else "")})
  return {"available":bool(devices),"devices":devices,"default_device":default.name if default else None,
          "error":None if devices else "Windows meldet keine Audioausgänge. Prüfe die Windows-Soundausgabe und starte DanceDetector neu."}
 except Exception as e:
  return {"available":False,"devices":[],"error":f"Audioausgänge konnten nicht ermittelt werden: {e}"}

def extract_features(audio, sample_rate):
 x=np.asarray(audio,dtype=np.float32)
 if x.ndim>1: x=x.mean(axis=1)
 if len(x)<sample_rate*2: return {}
 x=x-np.mean(x)
 if not np.isfinite(x).all() or np.max(np.abs(x))<1e-5: return {}
 # 2048-sample Hann windows, 50% overlap
 n=2048; hop=1024
 if len(x)<n: return {}
 frames=np.stack([x[i:i+n]*np.hanning(n) for i in range(0,len(x)-n+1,hop)])
 spec=np.abs(np.fft.rfft(frames,axis=1))
 power=spec**2
 freqs=np.fft.rfftfreq(n,1/sample_rate)
 total=power.sum(axis=1)+1e-12
 centroid=(spec*freqs).sum(axis=1)/(spec.sum(axis=1)+1e-12)
 bandwidth=np.sqrt((spec*(freqs[None,:]-centroid[:,None])**2).sum(axis=1)/(spec.sum(axis=1)+1e-12))
 csum=np.cumsum(power,axis=1)
 roll_idx=(csum>=.85*total[:,None]).argmax(axis=1)
 rolloff=freqs[roll_idx]
 norm=spec/(np.linalg.norm(spec,axis=1,keepdims=True)+1e-12)
 flux=np.maximum(0,np.diff(norm,axis=0)).sum(axis=1)
 rms=np.sqrt(np.mean(frames**2,axis=1))
 # Onsets from positive spectral flux peaks
 novelty=np.r_[0,flux]
 threshold=np.mean(novelty)+.5*np.std(novelty)
 peaks=np.where((novelty[1:-1]>novelty[:-2])&(novelty[1:-1]>=novelty[2:])&(novelty[1:-1]>threshold))[0]+1
 duration=len(x)/sample_rate
 onset_density=len(peaks)/max(duration,1e-9)
 intervals=np.diff(peaks)*hop/sample_rate
 cv=0.0
 if len(intervals)>1:
  cv=float(np.std(intervals)/(np.mean(intervals)+1e-9))
  beat_reg=float(np.clip(1-cv,0,1))
  pairs=intervals[:len(intervals)//2*2].reshape(-1,2)
  swing=float(np.median(np.maximum(pairs[:,0],pairs[:,1])/(np.minimum(pairs[:,0],pairs[:,1])+1e-9))) if len(pairs) else None
  swing_norm=float(np.clip((swing-1)/1.0,0,1)) if swing is not None else None
 else: beat_reg=None; swing_norm=None
 low=power[:,freqs<250].sum(); mid=power[:,(freqs>=250)&(freqs<4000)].sum(); high=power[:,freqs>=4000].sum()
 bands=np.array([low,mid,high],dtype=float); bands/=bands.sum()+1e-12
 # harmonic/percussive proxy: temporal stability versus frame-to-frame change
 temporal=np.mean(np.abs(np.diff(spec,axis=0))) if len(spec)>1 else None
 harmonic=float(np.mean(spec)/(np.mean(spec)+temporal+1e-12)) if temporal is not None else None
 vals={
 "onset_strength":float(np.clip(np.mean(novelty)*20,0,1)),
 "onset_density":float(np.clip(onset_density/8,0,1)),
 "syncopation":float(np.clip(np.std(rms)/(np.mean(rms)+1e-9),0,1)),
 "beat_strength":float(np.clip(np.mean(rms)*8,0,1)),
 "beat_regularity":beat_reg, "swing_ratio":swing_norm,
 "rhythmic_complexity":float(np.clip(onset_density/8*.5+(cv if len(intervals)>1 else 0)*.5,0,1)),
 "spectral_centroid":float(np.clip(np.mean(centroid)/(sample_rate/2),0,1)),
 "spectral_bandwidth":float(np.clip(np.mean(bandwidth)/(sample_rate/2),0,1)),
 "spectral_rolloff":float(np.clip(np.mean(rolloff)/(sample_rate/2),0,1)),
 "spectral_flux":float(np.clip(np.mean(flux)*10,0,1)),
 "low_energy":float(bands[0]),"mid_energy":float(bands[1]),"high_energy":float(bands[2]),
 "harmonic_percussive_ratio":float(np.clip(harmonic,0,1)) if harmonic is not None else None,
 }
 # Compact MFCC vector (5 coefficients) from a triangular mel filter bank.
 try:
  from scipy.fft import dct
  mel=lambda hz:2595*np.log10(1+hz/700)
  hz=lambda m:700*(10**(m/2595)-1)
  edges=hz(np.linspace(mel(0),mel(sample_rate/2),12))
  filters=[]
  for j in range(1,len(edges)-1):
   filters.append(np.maximum(0,np.minimum((freqs-edges[j-1])/(edges[j]-edges[j-1]+1e-9),(edges[j+1]-freqs)/(edges[j+1]-edges[j]+1e-9))))
  mel_energy=np.maximum(power@np.array(filters).T,1e-10)
  mfcc=np.mean(dct(np.log(mel_energy),type=2,axis=1,norm="ortho")[:,1:6],axis=0)
  mfcc_values=[float(np.clip((v+20)/40,0,1)) for v in mfcc]
 except Exception: mfcc_values=None
 # A 4-slot accent pattern is estimated from envelope phase; useful as descriptive profile.
 if mfcc_values:
  for i,v in enumerate(mfcc_values,1): vals[f"mfcc_{i}"]=v
 accent=[float(np.clip(np.mean(v)*8,0,1)) for v in np.array_split(rms,min(4,len(rms))) if len(v)]
 for i,v in enumerate(accent,1): vals[f"accent_{i}"]=v
 vals["beat_accent_pattern"]=accent
 return vals

def _capture(device_name):
 global _state
 try:
  if sc is None: raise RuntimeError("Bitte 'pip install soundcard' ausführen.")
  speaker=next((s for s in sc.all_speakers() if s.name==device_name),None) if device_name else sc.default_speaker()
  if speaker is None: raise RuntimeError("Audioausgang nicht gefunden.")
  mic=sc.get_microphone(id=str(speaker.name), include_loopback=True)
  with mic.recorder(samplerate=22050, channels=2) as rec:
   start=time.time()
   while not _stop.is_set():
    chunk=rec.record(numframes=22050*5)
    features=extract_features(chunk,22050)
    with _lock:
     _state.update(signal=bool(features),features=features,seconds=round(time.time()-start,1),
                   phase=("early" if time.time()-start<10 else "refined"),error=None)
 except Exception as e:
  with _lock: _state["error"]=str(e)
 finally:
  with _lock: _state["running"]=False; _state["signal"]=False; _state["phase"]="idle"

def reset_song_features():
 """Discard measurements from the previous Spotify track without stopping capture."""
 with _lock:
  _state.update(signal=False,features={},seconds=0,phase="starting",error=None)


def start(device_name=None):
 global _thread
 with _lock:
  if _state["running"]: return dict(_state)
  _stop.clear(); _state.update(running=True,device=device_name,signal=False,features={},seconds=0,error=None,phase="starting")
  _thread=threading.Thread(target=_capture,args=(device_name,),daemon=True); _thread.start()
  return dict(_state)
def stop():
 _stop.set()
 with _lock: return dict(_state)
def status():
 with _lock: return dict(_state)
