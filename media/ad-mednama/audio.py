"""The medNAMA (core) ad's soundtrack and sound effects, synthesised from scratch in NumPy from the same timeline as the picture.

    python audio.py <out.wav> [spectrogram.png]

Arc: one question and three clashing answers -> the flood (pages flicking ever faster, books landing, a rising drone,
a clock) -> a hard stop -> a bell, the medNAMA motif (E-B-A-G) and a highlighter -> clarity (pads that change on the cuts,
a soft pulse, interface sounds; the wrong answer's dud, the remembered answer's chime) -> the motif resolves to E major.
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
    t = tvec(dur)
    low = np.sin(2 * np.pi * (45 + 30 * np.exp(-t * 12)) * t) * env(len(t), 0.002, 0.25)
    noise = lowpass(rng.standard_normal(len(t)), 2500) * env(len(t), 0.001, 0.06)
    return low * 0.9 + noise * 0.5


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


def flutter(dur=0.09):
    """A page flicked past: two quick bursts of paper noise, 1.5-7 kHz, with a 250-900 Hz edge a phone can play."""
    n = int(dur * SR)
    x = rng.standard_normal(n)
    e = env(n, 0.001, 0.012) + 0.6 * np.roll(env(n, 0.001, 0.018), int(0.03 * SR))
    hi = bandpass(x, 1500, 7000) * e
    body = bandpass(x, 250, 900) * e * 0.6
    return hi + body


def shimmer(dur=0.7):
    """The highlighter sweeping a line: filtered noise that rises and brightens."""
    n = int(dur * SR)
    y = bandpass(rng.standard_normal(n), 2500, 9000) * np.sin(np.linspace(0, np.pi, n)) ** 2
    return y * np.linspace(0.4, 1.0, n)


def build() -> np.ndarray:
    buf = np.zeros((N, 2))
    E5, G5, A5, B5, E6, G6, A6, B6 = 659.25, 783.99, 880.0, 987.77, 1318.51, 1567.98, 1760.0, 1975.53
    MOTIF = (E5, B5, A5, G5)                            # the medNAMA motif, shared with the Paediatrics film
    sc = {s["id"]: s for s in TL["scenes"]}

    # 1. the question types; three unsourced answers land as clashing blips
    q0, q1 = TL["type_q"]
    for i, at in enumerate(np.arange(q0, q1, 0.034)):
        place(buf, click(0.018, 3400 + 400 * (i % 3)), float(at), 0.35, 0.2)
    s0, s1 = TL["swell"]                                # the flood is coming: a rising swell, not dead air
    n = int((s1 - s0) * SR)
    sw = bandpass(rng.standard_normal(n), 300, 5000) * np.linspace(0, 1, n) ** 2
    place(buf, sw, s0, 0.30)
    for i, at in enumerate(TL["bad_answers"]):
        f = (466.16, 493.88, 523.25)[i]                 # Bb4, B4, C5: three answers that disagree
        place(buf, pluck(f, 0.5, 0.18) + 0.6 * pluck(f * 1.06, 0.5, 0.18), at, 0.30, -0.5 + 0.5 * i)
        place(buf, bandpass(knock(0.1), 300, 600), at, 1.4, -0.5 + 0.5 * i)       # each lands with a knock
        place(buf, thump(0.25), at, 0.35)

    # 2. the flood: pages flicking past ever faster, books landing, a rising drone, a clock
    fl0, cut = sc["flood"]["start"], TL["freeze"]
    for i, at in enumerate(TL["flutters"]):
        k = (at - fl0) / (cut - fl0)
        place(buf, flutter(), at, 0.22 + 0.18 * k, float(rng.uniform(-0.8, 0.8)))
    for i, at in enumerate(TL["books"]):
        place(buf, thump(0.3), at, 0.45)
        place(buf, knock(), at, 0.60)
    place(buf, drone(cut - fl0, start_cut=500, end_cut=4500), fl0, 0.16)
    tk = fl0
    while tk < cut:
        place(buf, click(0.02, 3000), tk, 0.12, 0.6)
        tk += 0.5 - 0.25 * (tk - fl0) / (cut - fl0)
    place(buf, impact() + 0.5 * bandpass(rng.standard_normal(int(0.9 * SR)) * env(int(0.9 * SR), 0.001, 0.08), 250, 900), TL["countdown"], 0.45)
    for at in (4.0, 5.6):                               # the two captions
        place(buf, whoosh(0.5), at - 0.25, 0.26)
    buf[int(cut * SR):] = 0.0                            # the freeze: nothing crosses it
    place(buf, whoosh(0.35)[::-1], TL["fly_off"][0] - 0.03, 0.22)   # the pages leave

    # 3. the page: a bell, the motif, the highlighter
    place(buf, bell(E6, 2.2), TL["page"], 0.48)
    for f, at, last in zip(MOTIF, TL["motif"], (False, False, False, True)):
        place(buf, pluck(f, 3.0 if last else 1.0, 1.5 if last else 0.45), at, 0.46, -0.1)
        place(buf, pluck(f / 2, 3.0 if last else 1.0, 1.5 if last else 0.45), at, 0.40, 0.1)   # doubled an octave down: found
    h0, h1 = TL["highlight"]
    place(buf, shimmer(h1 - h0), h0, 0.20, 0.3)

    # 4-6. clarity: pads that change on the cuts, a soft pulse, interface sounds
    chords = [(220.0, 261.63, 329.63, 493.88), (174.61, 220.0, 261.63, 329.63), (130.81, 196.0, 246.94, 329.63)]
    edges = [sc["answer"]["start"], sc["recall"]["start"], sc["plan"]["start"], sc["end"]["start"]]
    for i, ch in enumerate(chords):                    # Am(add9) -> Fmaj7 -> Cmaj7
        place(buf, pad(ch, edges[i + 1] - edges[i] + 0.8, attack=0.4, release=0.8), edges[i], 0.17)
    for i, b in enumerate(TL["pulse"]):
        place(buf, thump(0.22), b, 0.14)
        place(buf, knock(), b, 0.13)
    a0, a1 = TL["type_a"]
    for i, at in enumerate(np.arange(a0, a1, 0.05)):
        place(buf, click(0.015, 3800 + 300 * (i % 3)), float(at), 0.10, 0.25)
    place(buf, click(0.03, 5200), TL["chip"], 0.34, 0.2)
    place(buf, pluck(B6, 0.8, 0.3), TL["chip"] + 0.01, 0.20, 0.2)
    place(buf, whoosh(0.5), TL["proof"] - 0.1, 0.20, 0.5)
    place(buf, note(G6, 0.12, 0.03, harmonics=(1.0,)), TL["caption"], 0.10)
    place(buf, click(0.03, 4800), TL["tap_wrong"], 0.32)
    place(buf, dud(), TL["wrong"], 0.50)
    place(buf, note(A5, 0.5, 0.15), TL["explain"], 0.12)
    place(buf, pluck(B5, 0.9, 0.35), TL["later"], 0.24)
    for i, d in enumerate(TL["days"]):
        place(buf, click(0.02, 3600 + 200 * i), d, 0.22, -0.3 + 0.2 * i)
    place(buf, whoosh(0.4), TL["returns"] - 0.2, 0.18)
    place(buf, click(0.03, 4800), TL["tap_right"], 0.32)
    place(buf, pluck(E6, 0.9, 0.35), TL["right"], 0.24, -0.1)   # the motif's own first notes: remembered
    place(buf, pluck(B6, 1.4, 0.6), TL["right"] + 0.16, 0.22, 0.1)
    for at in TL["plan_cards"]:
        place(buf, click(0.025, 5200), at, 0.28, 0.3)
        place(buf, note(G6, 0.12, 0.03, harmonics=(1.0,)), at + 0.01, 0.10, 0.3)

    # 7. the end: the whole motif, home to E major (arrival, not resignation)
    em = TL["end_motif"]
    for f, at, dur, dec in zip((E5, B5, A5, G5, E5), em, (1.0, 1.0, 1.2, 1.6, 3.4), (0.45, 0.45, 0.6, 0.9, 1.8)):
        place(buf, pluck(f, dur, dec), at, 0.25)
    place(buf, pad((164.81, 246.94, 329.63, 415.30), TL["duration"] - em[0] + 0.5, attack=0.6, release=2.0), em[0], 0.15)

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
    out = sosfilt(butter(2, 45, "high", fs=SR, output="sos"), out, axis=0)
    out[int(cut * SR):int((TL["fly_off"][0] - 0.03) * SR)] = 0.0   # the stop: real silence, reverb tails included
    fo0 = int(TL["fade_out"][0] * SR)
    out[fo0:] *= (np.linspace(1, 0, N - fo0) ** 1.5)[:, None]
    out /= np.max(np.abs(out)) + 1e-9
    out = np.tanh(1.8 * out) / np.tanh(1.8)
    return out / (np.max(np.abs(out)) + 1e-9) * 0.89


ARC = {"question": -6.0, "flood": 3.0, "page": 1.0, "answer": -3.0, "recall": -3.0, "plan": -3.0}
SILENT = ("freeze", "end")


def auto_balance(buf: np.ndarray, max_gain=6.0) -> np.ndarray:
    """Per-scene gains that set each scene's phone-band (200 Hz-4 kHz) level against the end card's, following ARC:
    a quiet monitor, chaos building to a peak at the pressure, a hushed turn, a steady middle, then the end.
    The silence stays silent; gains crossfade over 80 ms at the cuts."""
    mono = bandpass(buf.mean(axis=1), 200, 4000)
    rms = {s["id"]: np.sqrt(np.mean(mono[int(s["start"] * SR):int(s["end"] * SR)] ** 2)) + 1e-12 for s in TL["scenes"]}
    ref = rms["end"]
    gain = np.ones(N)
    for s in TL["scenes"]:
        if s["id"] in SILENT:
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
        if s["id"] in SILENT:
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
