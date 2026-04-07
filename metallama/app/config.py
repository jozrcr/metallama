from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")


class Config:
    EXECUTABLE_LLAMA = os.getenv("METALLAMA_LLAMACPP_BINARY", "")
    EXECUTABLE_WHISPER = os.getenv("METALLAMA_WHISPER_BINARY", "")
    EXECUTABLE_MINERU_VENV = os.getenv("METALLAMA_MINERU_VENV", "")
    BASE_URL = os.getenv("METALLAMA_BASE_URL", "http://gpu4.hygeos.com")


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
