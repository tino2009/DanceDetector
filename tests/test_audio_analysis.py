import numpy as np
from audio_analysis import extract_features

def test_features_on_synthetic_tone():
    sr=22050
    t=np.arange(sr*4)/sr
    x=0.2*np.sin(2*np.pi*440*t)
    result=extract_features(x,sr)
    assert "spectral_centroid" in result
    assert 0 <= result["spectral_centroid"] <= 1
    assert "low_energy" in result and "high_energy" in result
    assert len(result["beat_accent_pattern"]) > 0

def test_short_audio_returns_unavailable():
    assert extract_features(np.zeros(100),22050)=={}
