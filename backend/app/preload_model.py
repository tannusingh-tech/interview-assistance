"""Download/load the configured Whisper model ahead of time.

Run from the backend folder:  python -m app.preload_model
"""

import logging
import sys

from app.config import get_settings
from app.services.transcription import TranscriptionError, WhisperTranscriber


def main() -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        WhisperTranscriber(settings).ensure_loaded()
    except TranscriptionError as exc:
        print(f"ERROR: {exc.message}", file=sys.stderr)
        return 1
    print(f"Model '{settings.whisper_model}' is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
