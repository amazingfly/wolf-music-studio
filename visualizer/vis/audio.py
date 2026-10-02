import os
import librosa
import numpy as np


def extract_audio_features(
    audio_path: str,
    fps: int = 30,
    n_fft: int = 2048,
    mid_range: tuple = (180, 2000),
    high_range: tuple = (2000, 8000),
    percussion_decay: float = 0.30,
    energy_decay: float = 0.65,
    max_duration: float = None,
):
    """
    Extracts time-aligned audio transients and frequency band energies using librosa.
    """
    print(f"[*] Extracting audio transients: {os.path.basename(audio_path)}...")
    y, sr = librosa.load(audio_path, sr=None, duration=max_duration)
    hop_length = int(sr / fps)

    stft = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)

    if len(onset_env) < stft.shape[1]:
        onset_env = np.pad(onset_env, (0, stft.shape[1] - len(onset_env)))
    else:
        onset_env = onset_env[:stft.shape[1]]

    mn_o, mx_o = np.min(onset_env), np.max(onset_env)
    onset_norm = (onset_env - mn_o) / (mx_o - mn_o) if mx_o - mn_o > 1e-6 else np.zeros_like(onset_env)

    percussion_transients = np.zeros_like(onset_norm)
    curr = 0.0
    for i in range(len(onset_norm)):
        curr = max(onset_norm[i], curr * percussion_decay)
        percussion_transients[i] = curr

    mid_mask = (freqs > mid_range[0]) & (freqs <= mid_range[1])
    high_mask = (freqs > high_range[0]) & (freqs <= high_range[1])
    mid_energy = np.mean(stft[mid_mask, :], axis=0) if np.any(mid_mask) else np.zeros(stft.shape[1])
    high_energy = np.mean(stft[high_mask, :], axis=0) if np.any(high_mask) else np.zeros(stft.shape[1])

    def smooth_norm(arr, decay=energy_decay):
        smoothed = np.zeros_like(arr)
        c = 0.0
        for i in range(len(arr)):
            c = c * decay + arr[i] * (1.0 - decay)
            smoothed[i] = c
        mn, mx = np.min(smoothed), np.max(smoothed)
        return (smoothed - mn) / (mx - mn) if mx - mn > 1e-6 else np.zeros_like(smoothed)

    return {
        'percussion': percussion_transients,
        'mids': smooth_norm(mid_energy),
        'highs': smooth_norm(high_energy),
        'total_frames': stft.shape[1],
        'duration': len(y) / sr,
    }


def compute_frame_signals(
    audio_data: dict,
    fps: int = 30,
    k_exp: float = 3.8,
    morph_speed: float = 0.3,
    max_frames: int = None,
):
    """
    Computes vectorized per-frame time, exponential percussion response, and morphing signals.
    """
    total_frames = audio_data['total_frames']
    if max_frames and max_frames < total_frames:
        num_frames = max_frames
    else:
        num_frames = total_frames

    times = (np.arange(num_frames, dtype=np.float32) / fps)
    raw_perc = audio_data['percussion'][:num_frames]
    highs = audio_data['highs'][:num_frames]
    mids = audio_data['mids'][:num_frames]

    exp_denom = np.exp(k_exp) - 1.0
    perc_exps = ((np.exp(k_exp * (raw_perc ** 1.3)) - 1.0) / exp_denom).astype(np.float32)
    morph_params = ((np.sin(times * morph_speed) + 1.0) / 2.0).astype(np.float32)

    return {
        'num_frames': num_frames,
        'times': times,
        'perc_exps': perc_exps,
        'morph_params': morph_params,
        'highs': highs,
        'mids': mids,
    }
