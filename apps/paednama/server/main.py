"""PaedNAMA server:  python -m apps.paednama.server.main   (from the repo root), then the web app on :3100.

It serves medNAMA's own API (imported, not copied) with PaedNAMA's routes added under /api/paeds, so the web app
gets sign-in, the book library, questions, explanations and review cards from one process. medNAMA's files are not
edited: the routes are added to the imported app at start-up.
"""

from __future__ import annotations

import logging

from . import settings
from .models import create_tables
from .routes import router

from app.main import app  # noqa: E402  medNAMA's API

app.title = "PaedNAMA (on medNAMA)"
app.include_router(router)
create_tables()

logger = logging.getLogger("paednama")


def main() -> None:
    import uvicorn

    logger.info("PaedNAMA API on http://%s:%s", settings.HOST, settings.PORT)
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, timeout_keep_alive=70)


if __name__ == "__main__":
    main()
