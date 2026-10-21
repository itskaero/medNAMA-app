#!/usr/bin/env python3
"""Build standalone .exe applications for medNAMA."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_TEMPLATE = ROOT / "app-template"
DIST_DIR = APP_TEMPLATE / "dist"
DIST_DIR.mkdir(parents=True, exist_ok=True)

apps = [
    {
        "name": "Launch-medNAMA",
        "script": APP_TEMPLATE / "launch_mednama.py",
    },
    {
        "name": "Launch-RAG-Lab",
        "script": APP_TEMPLATE / "launch_rag_lab.py",
    },
    {
        "name": "Launch-Seed-Studio",
        "script": APP_TEMPLATE / "launch_seed_studio.py",
    },
    {
        "name": "Transfer-to-NAS",
        "script": APP_TEMPLATE / "transfer_to_nas.py",
    },
]

def build_app(app_info):
    name = app_info["name"]
    script = str(app_info["script"])
    print(f"\n==================================================")
    print(f"Building {name}.exe from {script}...")
    print(f"==================================================")

    html_file = str(APP_TEMPLATE / "pipeline_ui.html")
    template_py = str(APP_TEMPLATE / "ui_template.py")
    mp4_file = str(APP_TEMPLATE / "66688c8c-e2d8-450e-8952-fffa1f0279ce.mp4")

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", name,
        "--distpath", str(DIST_DIR),
        "--workpath", str(APP_TEMPLATE / "build" / name),
        "--specpath", str(APP_TEMPLATE / "build"),
        f"--add-data={html_file};.",
        f"--add-data={template_py};.",
        f"--add-data={mp4_file};.",
        script,
    ]

    res = subprocess.run(cmd, cwd=str(APP_TEMPLATE))
    if res.returncode != 0:
        print(f"Failed to build {name}.exe (exit code {res.returncode})")
        return False
    print(f"Successfully built {name}.exe in {DIST_DIR / name}")
    return True

def main():
    success = True
    for app in apps:
        ok = build_app(app)
        if not ok:
            success = False
            break

    if success:
        print("\nAll 4 apps built successfully!")
    else:
        print("\nSome builds failed.")

if __name__ == "__main__":
    main()

