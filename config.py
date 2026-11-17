"""Environment-only configuration. Never log configuration values or keys."""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
DATA_DIR = Path(os.getenv("SMARTGRADE_DATA_DIR", str(ROOT / "storage"))).resolve()
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
