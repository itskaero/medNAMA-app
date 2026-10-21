"""Build one version of the ad:  python build.py v1

Renders every frame (render.js, headless Chromium), synthesises the soundtrack (audio.py), muxes them into
build/<version>/medNAMA-ad-<version>.mp4 (H.264 + AAC), and makes the review material: a contact sheet (one frame
every second), the spectrogram, and a short README for the critic.

Needs: Playwright on NODE_PATH, and ffmpeg (FFMPEG env, else imageio-ffmpeg's binary on PYTHONPATH).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def ffmpeg() -> str:
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def main(version: str) -> None:
    out = HERE / "build" / version
    frames = out / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["node", str(HERE / "render.js"), str(frames), "30", "1"], check=True)
    subprocess.run([sys.executable, str(HERE / "audio.py"), str(out / "audio.wav"), str(out / "spectrogram.png")], check=True)
    ff = ffmpeg()
    mp4 = out / f"medNAMA-ad-{version}.mp4"
    subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", "30", "-i", str(frames / "%04d.jpg"), "-i", str(out / "audio.wav"),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(mp4)], check=True)
    # contact sheet: one frame per second, 6 x 5, each 480 x 270, with the second number burned in
    subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", "30", "-i", str(frames / "%04d.jpg"),
                    "-vf", "select='not(mod(n\\,30))',scale=480:270,tile=6x5:padding=6:color=0x222222", "-frames:v", "1",
                    str(out / "contact_sheet.jpg")], check=True)
    # detail strips around the key moments (every 5th frame) for timing review
    import json
    tl = json.loads((HERE / "timeline.json").read_text(encoding="utf-8"))
    sc = {x["id"]: x["start"] for x in tl["scenes"]}
    moments = [("into_the_turn", sc.get("silence", sc.get("turn", 10)) - 0.6), ("structure", sc.get("grid", sc.get("structure", 14)) - 0.2),
               ("exam_to_end", sc["end"] - 1.2)]
    for name, start in moments:
        f0 = int(start * 30)
        subprocess.run([ff, "-y", "-loglevel", "error", "-start_number", str(f0), "-framerate", "30", "-i", str(frames / "%04d.jpg"),
                        "-vf", "select='not(mod(n\\,5))',scale=384:216,tile=6x2:padding=4:color=0x222222", "-frames:v", "1",
                        str(out / f"strip_{name}.jpg")], check=True)
    print("built", mp4)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v1")
