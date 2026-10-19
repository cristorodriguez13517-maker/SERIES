import os
import re
import json
import time
import requests
import unicodedata
from difflib import SequenceMatcher

# ==========================================
# CONFIGURACIÓN DE YOUTUBE Y TMDB
# ==========================================

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
TMDB_KEY = os.getenv("TMDB_API_KEY", "").strip()

PLAYLIST_ID = os.getenv(
    "YOUTUBE_SERIES_PLAYLIST_ID",
    "PLAeNuSpPHAsk"
).strip()

YAPI = "https://www.googleapis.com/youtube/v3/playlistItems"

if not YOUTUBE_KEY:
    raise SystemExit(
        "ERROR: falta la variable YOUTUBE_API_KEY en GitHub Secrets."
    )

if not TMDB_KEY:
    raise SystemExit(
        "ERROR: falta la variable TMDB_API_KEY en GitHub Secrets."
    )

if not PLAYLIST_ID:
    raise SystemExit(
        "ERROR: el ID de la lista de reproducción de YouTube está vacío."
    )

print("Configuración cargada correctamente.")
print(f"Lista de series configurada: {PLAYLIST_ID}")
