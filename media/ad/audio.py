"""The ad's soundtrack and sound effects, synthesised from scratch in NumPy from the same timeline as the picture.

    python audio.py <out.wav> [spectrogram.png]

Arc: a calm monitor beep -> controlled chaos (a heart rate that climbs, a detuned drone with a rising filter, whooshes,
impacts) -> a hard cut to silence -> a bell and the motif (E-B-A-G) -> clarity (a warm pad over a steady pulse, precise
interface clicks and confirmation chimes) -> the motif resolves.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

SR = 48_000
HERE = Path(__file__).parent
TL = json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))
N = int(TL["duration"] * SR)
rng = np.random.default_rng(560)


def tvec(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR)) / SR


def place(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0, pan: float = 0.0) -> None:
    """Add a mono signal into the stereo buffer at time `at` (equal-power pan, -1 left .. 1 right)."""
    i = int(at * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    left, right = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    buf[i:i + len(sig), 0] += sig * left
    buf[i:i + len(sig), 1] += sig * right


def env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-np.maximum(t - attack, 0) / decay)


def note(freq: float, dur: float, decay: float, harmonics=(1.0, 0.35, 0.12), attack=0.004) -> np.ndarray:
    t = tvec(dur)
    s = sum(a * np.sin(2 * np.pi * freq * (k + 1) * t) for k, a in enumerate(harmonics))
    return s * env(len(t), attack, decay)


def lowpass(x: np.ndarray, hz: float) -> np.ndarray:
    return sosfilt(butter(2, hz, "low", fs=SR, output="sos"), x)


def bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return sosfilt(butter(2, [lo, hi], "band", fs=SR, output="sos"), x)


# ── instruments ──────────────────────────────────────────────────────────────
def beep(freq=960.0, dur=0.10):
    return note(freq, dur, 0.035, harmonics=(1.0, 0.04), attack=0.002)


def thump(dur=0.35):
    t = tvec(dur)
    f = 62 * np.exp(-t * 9) + 40
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), 0.003, 0.09)


def click(dur=0.03, hz=4200):
    x = rng.standard_normal(int(dur * SR)) * env(int(dur * SR), 0.0005, 0.004)
    return bandpass(x, hz * 0.6, min(hz * 1.6, SR / 2 - 100))


def whoosh(dur=0.7):
    n = int(dur * SR)
    x = rng.standard_normal(n)
    sweep = np.linspace(400, 5000, n)
    y = np.zeros(n)
    for a in range(0, n, 2400):                        # a band that rises, block by block
        b = min(n, a + 2400)
        y[a:b] = bandpass(x[a:b], sweep[a] * 0.7, min(sweep[a] * 1.4, SR / 2 - 100))
    return y * np.sin(np.linspace(0, np.pi, n)) ** 2


def impact(dur=0.9):
    """A hit with weight a phone can play: a falling low tone (high-passed at 70 Hz), noise, and a detuned
    square-wave body in 400 Hz - 2 kHz."""
    t = tvec(dur)
    low = np.sin(2 * np.pi * (45 + 30 * np.exp(-t * 12)) * t) * env(len(t), 0.002, 0.25)
    low = sosfilt(butter(2, 70, "high", fs=SR, output="sos"), low)
    noise = lowpass(rng.standard_normal(len(t)), 2500) * env(len(t), 0.001, 0.06)
    sq = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (311.1, 329.6, 349.2))
    body = bandpass(sq, 400, 2000) * env(len(t), 0.002, 0.12)
    return low * 0.9 + noise * 0.5 + body * 0.35


def bell(freq=1318.5, dur=2.8):
    t = tvec(dur)
    partials = [(1.0, 1.0, 1.6), (2.76, 0.4, 0.9), (5.4, 0.18, 0.5), (8.93, 0.08, 0.3)]
    return sum(a * np.sin(2 * np.pi * freq * r * t) * np.exp(-t / d) for r, a, d in partials) * env(len(t), 0.003, 9)


def pluck(freq, dur=1.6, decay=0.55):
    """The motif's voice: a soft electric-piano-like tone (FM); `decay` > 1 s holds the note."""
    t = tvec(dur)
    mod = np.sin(2 * np.pi * freq * 2 * t) * 1.6 * np.exp(-t * 5)
    return np.sin(2 * np.pi * freq * t + mod) * env(len(t), 0.005, decay)


def dud(dur=0.5):
    """The missed criterion: a muted, detuned pair (G4 against G#4) with a dry knock, in the band a phone plays."""
    t = tvec(dur)
    tone = (np.sin(2 * np.pi * 392.0 * t) + 0.7 * np.sin(2 * np.pi * 415.3 * t)) * env(len(t), 0.003, 0.11)
    knockn = bandpass(rng.standard_normal(len(t)), 300, 1500) * env(len(t), 0.0005, 0.02)
    return bandpass(tone + 0.8 * knockn, 300, 1500)


def pad(freqs, dur, attack=1.2, release=1.5):
    t = tvec(dur)
    s = np.zeros_like(t)
    for f in freqs:
        for det in (-0.12, 0.0, 0.11):                  # three slightly detuned voices per note
            s += np.sin(2 * np.pi * f * (1 + det / 100) * t + rng.uniform(0, 6.28))
    s = lowpass(s / (3 * len(freqs)), 1800)
    shape = np.clip(t / attack, 0, 1) * np.clip((dur - t) / release, 0, 1)
    return s * shape


def drone(dur, start_cut=300, end_cut=4000):
    """Controlled chaos: a detuned saw cluster whose filter opens as the pressure builds."""
    t = tvec(dur)
    s = np.zeros_like(t)
    for f in (110.0, 116.5, 164.8, 233.1):              # A2, Bb2 (rub), E3, Bb3: tense
        s += 2 * ((f * t) % 1.0) - 1
    s /= 4
    out = np.zeros_like(s)
    cuts = np.geomspace(start_cut, end_cut, 64)
    edges = np.linspace(0, len(s), 65).astype(int)
    for k in range(64):
        a, b = edges[k], edges[k + 1]
        out[a:b] = lowpass(s[max(0, a - 2000):b], cuts[k])[-(b - a):]
    return out * np.clip(t / 0.8, 0, 1)


def reverb(x: np.ndarray, seconds=1.6, mix=0.22) -> np.ndarray:
    n = int(seconds * SR)
    ir = np.random.default_rng(7).standard_normal(n) * np.exp(-np.arange(n) / (SR * seconds / 6))
    ir = lowpass(ir, 6000)
    ir /= np.sqrt(np.sum(ir ** 2))
    L = len(x) + n
    nfft = 1 << (L - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft), nfft)[: len(x)]
    return x * (1 - mix) + wet * mix


# ── the score ────────────────────────────────────────────────────────────────
def knock(dur=0.12):
    """A 180-250 Hz body under each heartbeat, so the pulse survives phone speakers."""
    t = tvec(dur)
    return np.sin(2 * np.pi * (240 - 60 * t / dur) * t) * env(len(t), 0.002, 0.03)


def build() -> np.ndarray:
    buf = np.zeros((N, 2))
    E5, G5, A5, B5, E6, G6, A6, B6 = 659.25, 783.99, 880.0, 987.77, 1318.51, 1567.98, 1760.0, 1975.53
    MOTIF = (E5, B5, A5, G5)                            # E  B  A  .  G--  (dotted; the G is held)
    sc = {s["id"]: s for s in TL["scenes"]}

    for b in TL["beats_calm"]:                          # night duty: a calm monitor
        if b >= 0:
            place(buf, beep(), b, 0.30, 0.2)
    rising = TL["beats_rising"]
    for i, b in enumerate(rising):                     # the trainee's pulse climbs; the tone falls, as on a monitor when it gets worse
        k = i / max(1, len(rising) - 1)
        place(buf, beep(960 - 160 * k), b, 0.26 + 0.08 * k, 0.2)
        place(buf, thump(), b, 0.40 + 0.3 * k)
        place(buf, knock(), b, 0.35 + 0.15 * k)         # the pulse a phone speaker can play
    chaos_start, cut = sc["chaos"]["start"], TL["cut_to_silence"]
    place(buf, drone(cut - chaos_start, start_cut=700, end_cut=5000), chaos_start, 0.20)
    for i, w in enumerate(TL["type_words"]):
        place(buf, whoosh(), w - 0.25, 0.34, -0.4 + 0.4 * i)
    for i, f in enumerate(TL["flash"]):
        place(buf, impact(), f, 0.55 + 0.1 * i)
        place(buf, click(0.06, 1800), f, 0.35)          # a mid-band edge on each hit
    tick_t = chaos_start
    while tick_t < cut:                                 # a clock under it all
        place(buf, click(0.02, 3000), tick_t, 0.14, 0.6)
        tick_t += 0.5
    buf[int(cut * SR):] = 0.0                            # the hard cut: nothing crosses it

    # the turn: the phone lights, the motif, keystrokes, the fragments pulled into the card
    place(buf, bell(E6, 2.2), TL["phone"], 0.08)
    for f, at, last in zip(MOTIF, TL["motif"], (False, False, False, True)):
        place(buf, pluck(f, 3.0 if last else 1.0, 1.5 if last else 0.45), at, 0.46, -0.1)
    f0, f1 = TL["hero_fly"]
    place(buf, whoosh(f1 - f0 + 0.1), f0, 0.22, -0.3)
    place(buf, click(0.03, 3600), f1, 0.34, 0.1)          # the note snaps into the search bar
    place(buf, note(E6, 0.25, 0.06), f1 + 0.01, 0.10)
    place(buf, whoosh(0.5)[::-1], TL["pull"], 0.30)
    place(buf, note(B5, 0.5, 0.15), TL["result"], 0.16)
    place(buf, pad((246.94, 369.99, 493.88), sc["turn"]["end"] - TL["result"] + 0.4, attack=0.3, release=0.4), TL["result"], 0.16)

    clarity, end_start, end = sc["grid"]["start"], sc["end"]["start"], TL["duration"]
    chords = [(220.0, 261.63, 329.63, 493.88), (174.61, 220.0, 261.63, 329.63), (130.81, 196.0, 246.94, 329.63), (164.81, 196.0, 246.94, 293.66)]
    edges = TL["chords"]
    for i, ch in enumerate(chords):                    # Am9 -> Fmaj7 -> Cmaj7 -> Em7, each change on a cut
        place(buf, pad(ch, edges[i + 1] - edges[i] + 0.8, attack=0.4, release=0.8), edges[i], 0.18 if i < 3 else 0.32)
    for at in TL["late_pulse"]:                         # the pulse quickens into the end card
        place(buf, knock(), at, 0.45)
        place(buf, thump(0.2), at, 0.30)
        place(buf, beep(880, 0.07), at, 0.12, 0.4)
    for i, b in enumerate(TL["beats_steady"]):         # steady now, on the cut grid; the beep every other beat
        place(buf, thump(0.25), b, 0.20)
        place(buf, knock(), b, 0.22)
        if i % 2 == 0:
            place(buf, beep(880, 0.07), b, 0.09, 0.4)
    for u in TL["ui"]:
        place(buf, click(0.025, 5200), u, 0.32, 0.3)
        place(buf, note(G6, 0.12, 0.03, harmonics=(1.0,)), u + 0.01, 0.10, 0.3)
    # the criteria quote the motif in its own voice, an octave up; the miss is a dud where the G should be
    order = TL["tick_order"]
    for slot, (tk, crit) in enumerate(zip(TL["ticks"], order)):
        if crit == TL["missed"]:
            place(buf, dud(), tk, 0.50)
            place(buf, pluck(G6, 2.4, 1.2), tk + 0.35, 0.14, 0.2)   # a held question the end's E answers
        else:
            place(buf, pluck((E6, B6, A6, G6)[slot], 0.9, 0.35), tk, 0.30, -0.2 + 0.13 * slot)
    place(buf, note(B5, 0.6, 0.2), TL["card"], 0.16)

    # the end: the whole motif at last, then home to E (the wordmark lands on the first note)
    em = TL["end_motif"]
    for f, at, dur, dec in zip((E5, B5, A5, G5, E5), em, (1.0, 1.0, 1.2, 0.7, 3.4), (0.45, 0.45, 0.6, 0.25, 1.8)):
        place(buf, pluck(f, dur, dec), at, 0.24)
    place(buf, pluck(830.61, 3.4, 1.8), em[-1], 0.16)      # G#5 with the final E: the major third is heard
    place(buf, pad((164.81, 246.94, 329.63, 415.30), end - em[0] + 0.5, attack=0.4, release=2.0), em[0], 0.15)   # E major: arrival

    gain, best = auto_balance(buf), None
    for _ in range(5):                                  # the limiter moves levels: measure after it, correct by half, keep the best
        out = master(buf * gain[:, None], cut)
        err = arc_error(out)
        if best is None or err < best[0]:
            best = (err, out)
        gain = gain * np.sqrt(correction(out))
    return best[1]


def master(buf: np.ndarray, cut: float) -> np.ndarray:
    out = np.stack([reverb(buf[:, 0]), reverb(buf[:, 1])], axis=1)
    out[int(cut * SR):int(TL["phone"] * SR)] = 0.0     # real silence, reverb tails included
    out = sosfilt(butter(2, 45, "high", fs=SR, output="sos"), out, axis=0)   # sub rumble only steals headroom
    out[int(cut * SR):int(TL["phone"] * SR)] = 0.0     # real silence again after the filter
    fo0 = int(TL["fade_out"][0] * SR)
    out[fo0:] *= (np.linspace(1, 0, N - fo0) ** 1.5)[:, None]    # fades with the picture
    out /= np.max(np.abs(out)) + 1e-9
    out = np.tanh(1.8 * out) / np.tanh(1.8)             # a soft limiter: the hits stop setting the level
    return out / (np.max(np.abs(out)) + 1e-9) * 0.89     # about -1 dBFS


ARC = {"monitor": -8.0, "chaos": -3.0, "pressure": 4.5, "turn": -6.0, "grid": -4.0, "shifa": -4.0, "exam": -4.0}


def auto_balance(buf: np.ndarray, max_gain=6.0) -> np.ndarray:
    """Per-scene gains that set each scene's phone-band (200 Hz-4 kHz) level against the end card's, following ARC:
    a quiet monitor, chaos building to a peak at the pressure, a hushed turn, a steady middle, then the end.
    The silence stays silent; gains crossfade over 80 ms at the cuts."""
    mono = bandpass(buf.mean(axis=1), 200, 4000)
    rms = {s["id"]: np.sqrt(np.mean(mono[int(s["start"] * SR):int(s["end"] * SR)] ** 2)) + 1e-12 for s in TL["scenes"]}
    ref = rms["end"]
    gain = np.ones(N)
    for s in TL["scenes"]:
        if s["id"] in ("silence", "end"):
            continue
        want = ref * 10 ** (ARC[s["id"]] / 20)
        gain[int(s["start"] * SR):int(s["end"] * SR)] = float(np.clip(want / rms[s["id"]], 1 / max_gain, max_gain))
    k = int(0.08 * SR)
    return np.convolve(gain, np.ones(k) / k, mode="same")


def arc_error(out: np.ndarray) -> float:
    """Worst distance (dB) of any scene from its ARC offset."""
    rows = dict(phone_band_report(out))
    return max(abs(ARC[k] - (v - rows["end"])) for k, v in rows.items() if k in ARC)


def correction(out: np.ndarray) -> np.ndarray:
    """Per-scene gain that moves each scene's measured phone-band level toward its ARC offset from the end card."""
    rows = dict(phone_band_report(out))
    gain = np.ones(N)
    for s in TL["scenes"]:
        if s["id"] in ("silence", "end"):
            continue
        err = ARC[s["id"]] - (rows[s["id"]] - rows["end"])
        gain[int(s["start"] * SR):int(s["end"] * SR)] = 10 ** (np.clip(err, -6, 6) / 20)
    k = int(0.08 * SR)
    return np.convolve(gain, np.ones(k) / k, mode="same")


def phone_band_report(x: np.ndarray) -> list[tuple[str, float]]:
    """RMS (dBFS) per scene in 200 Hz - 4 kHz, the band a phone speaker plays. Target: every scene within 6 dB of the end."""
    mono = bandpass(x.mean(axis=1), 200, 4000)
    rows = []
    for s in TL["scenes"]:
        seg = mono[int(s["start"] * SR):int(s["end"] * SR)]
        rows.append((s["id"], 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9)))
    return rows


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR), w.writeframes(pcm.tobytes())


def spectrogram(x: np.ndarray, path: Path) -> None:
    """A log-frequency spectrogram with second markers and scene boundaries, for review."""
    from PIL import Image, ImageDraw

    mono = x.mean(axis=1)
    hop, win = 480, 4096
    frames = (len(mono) - win) // hop
    window = np.hanning(win)
    spec = np.array([np.abs(np.fft.rfft(mono[i * hop:i * hop + win] * window)) for i in range(frames)]).T
    freqs = np.fft.rfftfreq(win, 1 / SR)
    rows = np.geomspace(30, 16000, 360)
    idx = np.searchsorted(freqs, rows)
    img = 20 * np.log10(spec[idx] + 1e-6)
    img = np.clip((img - img.max() + 80) / 80, 0, 1)[::-1]
    rgb = (np.stack([img ** 0.8, img ** 1.6, img ** 3.0], axis=-1) * 255).astype(np.uint8)
    im = Image.fromarray(rgb).resize((1500, 360))
    d = ImageDraw.Draw(im)
    for s in range(0, int(TL["duration"]) + 1):
        x = s / TL["duration"] * 1500
        d.line([(x, 350), (x, 360)], fill=(255, 255, 255))
        if s % 5 == 0:
            d.text((x + 2, 2), f"{s}s", fill=(255, 255, 255))
    for sc in TL["scenes"]:
        x = sc["start"] / TL["duration"] * 1500
        d.line([(x, 0), (x, 360)], fill=(255, 90, 31))
        d.text((x + 3, 340), sc["id"], fill=(255, 140, 90))
    im.save(path)


if __name__ == "__main__":
    out = Path(sys.argv[1])
    x = build()
    write_wav(out, x)
    if len(sys.argv) > 2:
        spectrogram(x, Path(sys.argv[2]))
    rows = phone_band_report(x)
    ref = dict(rows)["end"]
    lines = [f"  {k:10s} {v:6.1f} dBFS  ({v - ref:+.1f})" for k, v in rows]
    report = "\n".join(["phone band (200 Hz-4 kHz) RMS by scene, dB vs the end:", *lines])
    print(report)
    (out.parent / "loudness.txt").write_text(report + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(x) / SR:.1f}s)")
