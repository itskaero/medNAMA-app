import base64
from pathlib import Path

_DIR = Path(__file__).resolve().parent
_HTML_FILE = _DIR / "pipeline_ui.html"
_MP4_FILE = _DIR / "66688c8c-e2d8-450e-8952-fffa1f0279ce.mp4"

if _HTML_FILE.exists():
    raw_html = _HTML_FILE.read_text(encoding="utf-8")
    if _MP4_FILE.exists():
        try:
            b64_video = base64.b64encode(_MP4_FILE.read_bytes()).decode("ascii")
            data_uri = f"data:video/mp4;base64,{b64_video}"
            raw_html = raw_html.replace('id="bgVideoSource" src=""', f'id="bgVideoSource" src="{data_uri}"')
            raw_html = raw_html.replace('id="bgVideo" style="display: none;"', 'id="bgVideo"')
        except Exception:
            pass
    HTML_CONTENT = raw_html
else:
    HTML_CONTENT = """<!DOCTYPE html><html><body style="background:#070a12;color:#fff;">Pipeline UI Loading...</body></html>"""

