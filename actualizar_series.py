import os
import re
import json
import time
import unicodedata
from datetime import datetime, timezone

import requests

# =========================================================
# CONFIGURACIÓN
# =========================================================

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
TMDB_KEY = os.getenv("TMDB_API_KEY", "").strip()

PLAYLIST_ID = os.getenv(
    "YOUTUBE_SERIES_PLAYLIST_ID",
    "PLAeNuSpPHAsk"
).strip()

YOUTUBE_API = "https://www.googleapis.com/youtube/v3/playlistItems"
TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/"

OUTPUT_SERIES = "series.json"
OUTPUT_EPISODES = "episodios.json"

# Pausa breve entre consultas a TMDB.
REQUEST_PAUSE = 0.25

session = requests.Session()
session.headers.update({"User-Agent": "SharkTV-Series-Updater/1.0"})

if not YOUTUBE_KEY:
    raise SystemExit(
        "ERROR: falta el secreto YOUTUBE_API_KEY en GitHub."
    )

if not TMDB_KEY:
    raise SystemExit(
        "ERROR: falta el secreto TMDB_API_KEY en GitHub."
    )

if not PLAYLIST_ID:
    raise SystemExit(
        "ERROR: el ID de la lista de YouTube está vacío."
    )


# =========================================================
# FUNCIONES GENERALES
# =========================================================

def get_json(url, params=None):
    """Realiza una petición HTTP y devuelve el JSON."""
    try:
        response = session.get(
            url,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        return response.json()

    except requests.HTTPError as exc:
        detalle = ""
        if exc.response is not None:
            detalle = exc.response.text[:500]

        raise RuntimeError(
            f"Error HTTP al consultar {url}: {exc}. {detalle}"
        ) from exc

    except requests.RequestException as exc:
        raise RuntimeError(
            f"Error de conexión al consultar {url}: {exc}"
        ) from exc


def normalizar(texto):
    """Normaliza texto para comparar títulos."""
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(
        c for c in texto if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", texto).strip().casefold()


def guardar_json(ruta, datos):
    """Guarda JSON UTF-8 legible."""
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(
            datos,
            archivo,
            ensure_ascii=False,
            indent=2
        )
        archivo.write("\n")


def imagen_tmdb(ruta, tamano="w500"):
    if not ruta:
        return None
    return f"{TMDB_IMAGE}{tamano}{ruta}"


# =========================================================
# LEER VÍDEOS DE LA LISTA DE YOUTUBE
# =========================================================

def youtube_videos():
    videos = []
    page_token = None
    vistos = set()

    while True:
        parametros = {
            "part": "snippet,contentDetails",
            "playlistId": PLAYLIST_ID,
            "maxResults": 50,
            "key": YOUTUBE_KEY,
        }

        if page_token:
            parametros["pageToken"] = page_token

        datos = get_json(YOUTUBE_API, parametros)

        for item in datos.get("items", []):
            snippet = item.get("snippet", {})
            detalles = item.get("contentDetails", {})

            video_id = detalles.get("videoId")

            if not video_id or video_id in vistos:
                continue

            vistos.add(video_id)

            titulo = (snippet.get("title") or "").strip()

            # YouTube puede devolver elementos eliminados o privados.
            if not titulo or titulo.lower() in {
                "deleted video",
                "private video",
                "vídeo eliminado",
                "video eliminado",
                "vídeo privado",
                "video privado",
            }:
                continue

            videos.append({
                "video_id": video_id,
                "youtube_url": (
                    f"https://www.youtube.com/watch?v={video_id}"
                ),
                "youtube_title": titulo,
                "published_at": (
                    detalles.get("videoPublishedAt")
                    or snippet.get("publishedAt")
                ),
                "position": snippet.get("position"),
            })

        page_token = datos.get("nextPageToken")

        if not page_token:
            break

    return videos


# =========================================================
# IDENTIFICAR SERIE, TEMPORADA Y EPISODIO
# Formato admitido:
# Los dinosaurios (2026) S01E01 - Ascenso
# Los dinosaurios S01E02 - El viaje
# =========================================================

PATRON_EPISODIO = re.compile(
    r"^(?P<serie>.+?)\s+"
    r"(?:\((?P<anio>\d{4})\)\s*)?"
    r"S(?P<temporada>\d{1,2})"
    r"E(?P<episodio>\d{1,3})"
    r"(?:\s*[-–—:]\s*(?P<titulo>.*))?$",
    re.IGNORECASE
)


def analizar_titulo(titulo):
    titulo_limpio = re.sub(r"\s+", " ", titulo).strip()
    coincidencia = PATRON_EPISODIO.match(titulo_limpio)

    if not coincidencia:
        return None

    nombre_serie = coincidencia.group("serie").strip()
    anio = coincidencia.group("anio")
    titulo_episodio = (
        coincidencia.group("titulo") or ""
    ).strip()

    # Limpia separadores sobrantes al final del nombre.
    nombre_serie = re.sub(r"[\s\-–—:]+$", "", nombre_serie).strip()

    if not nombre_serie:
        return None

    return {
        "serie": nombre_serie,
        "anio": int(anio) if anio else None,
        "temporada": int(coincidencia.group("temporada")),
        "episodio": int(coincidencia.group("episodio")),
        "titulo_episodio": titulo_episodio,
    }


# =========================================================
# CONSULTAR TMDB
# =========================================================

def tmdb_get(ruta, parametros=None):
    params = dict(parametros or {})
    params["api_key"] = TMDB_KEY
    params.setdefault("language", "es-ES")

    resultado = get_json(
        f"{TMDB_API}{ruta}",
        params
    )

    time.sleep(REQUEST_PAUSE)
    return resultado


def buscar_serie_tmdb(nombre, anio=None):
    parametros = {
        "query": nombre,
        "include_adult": "false",
    }

    if anio:
        parametros["first_air_date_year"] = anio

    resultado = tmdb_get("/search/tv", parametros)
    candidatos = resultado.get("results", [])

    if not candidatos and anio:
        # Segundo intento sin año por si TMDB registra otra fecha.
        resultado = tmdb_get(
            "/search/tv",
            {
                "query": nombre,
                "include_adult": "false",
            }
        )
        candidatos = resultado.get("results", [])

    if not candidatos:
        print(
            f"AVISO: no se encontró en TMDB la serie "
            f"'{nombre}' ({anio or 'sin año'})."
        )
        return None

    nombre_buscado = normalizar(nombre)

    # Prioriza coincidencia exacta de título en español o título original.
    exactos = [
        serie for serie in candidatos
        if nombre_buscado in {
            normalizar(serie.get("name")),
            normalizar(serie.get("original_name")),
        }
    ]

    if exactos:
        if anio:
            por_anio = [
                serie for serie in exactos
                if (serie.get("first_air_date") or "").startswith(
                    str(anio)
                )
            ]
            if por_anio:
                return por_anio[0]

        return exactos[0]

    # Si no hay coincidencia exacta, TMDB suele ordenar por relevancia.
    if anio:
        por_anio = [
            serie for serie in candidatos
            if (serie.get("first_air_date") or "").startswith(
                str(anio)
            )
        ]
        if por_anio:
            return por_anio[0]

    return candidatos[0]


def obtener_detalle_serie(serie_tmdb):
    tmdb_id = serie_tmdb["id"]

    detalle = tmdb_get(
        f"/tv/{tmdb_id}",
        {}
    )

    generos = [
        genero.get("name")
        for genero in detalle.get("genres", [])
        if genero.get("name")
    ]

    temporadas = []

    for temporada in detalle.get("seasons", []):
        temporadas.append({
            "numero": temporada.get("season_number"),
            "nombre": temporada.get("name"),
            "sinopsis": temporada.get("overview") or "",
            "poster": imagen_tmdb(
                temporada.get("poster_path"),
                "w500"
            ),
            "episodios": temporada.get("episode_count", 0),
        })

    return {
        "tmdb_id": tmdb_id,
        "titulo": (
            detalle.get("name")
            or serie_tmdb.get("name")
            or ""
        ),
        "titulo_original": (
            detalle.get("original_name")
            or serie_tmdb.get("original_name")
            or ""
        ),
        "sinopsis": detalle.get("overview") or "",
        "poster": imagen_tmdb(
            detalle.get("poster_path"),
            "w500"
        ),
        "fondo": imagen_tmdb(
            detalle.get("backdrop_path"),
            "original"
        ),
        "fecha_estreno": detalle.get("first_air_date") or "",
        "generos": generos,
        "temporadas_tmdb": temporadas,
        "valoracion": detalle.get("vote_average"),
        "numero_temporadas_tmdb": detalle.get("number_of_seasons", 0),
        "numero_episodios_tmdb": detalle.get("number_of_episodes", 0),
    }


# =========================================================
# CONSTRUIR LOS CATÁLOGOS
# =========================================================

def main():
    print("==========================================")
    print(" ACTUALIZADOR DE SERIES SHARKTV")
    print("==========================================")
    print(f"Lista de YouTube: {PLAYLIST_ID}")

    videos = youtube_videos()
    print(f"Vídeos leídos de YouTube: {len(videos)}")

    series_por_nombre = {}
    episodios = []
    ids_video_procesados = set()

    # Se analiza cada título de YouTube.
    for video in videos:
        datos = analizar_titulo(video["youtube_title"])

        if not datos:
            print(
                "AVISO: título no reconocido, se omite: "
                f"{video['youtube_title']}"
            )
            continue

        clave_nombre = normalizar(datos["serie"])

        if clave_nombre not in series_por_nombre:
            resultado_tmdb = buscar_serie_tmdb(
                datos["serie"],
                datos["anio"]
            )

            if resultado_tmdb:
                detalle = obtener_detalle_serie(resultado_tmdb)
            else:
                # No se pierde el episodio si TMDB no encuentra la serie.
                detalle = {
                    "tmdb_id": None,
                    "titulo": datos["serie"],
                    "titulo_original": datos["serie"],
                    "sinopsis": "",
                    "poster": None,
                    "fondo": None,
                    "fecha_estreno": (
                        str(datos["anio"]) if datos["anio"] else ""
                    ),
                    "generos": [],
                    "temporadas_tmdb": [],
                    "valoracion": None,
                    "numero_temporadas_tmdb": 0,
                    "numero_episodios_tmdb": 0,
                }

            # ID estable para relacionar la serie con sus episodios.
            if detalle["tmdb_id"] is not None:
                id_serie = f"tmdb-{detalle['tmdb_id']}"
            else:
                id_serie = (
                    "local-"
                    + re.sub(r"[^a-z0-9]+", "-", clave_nombre).strip("-")
                )

            detalle["id"] = id_serie
            detalle["nombre_detectado"] = datos["serie"]
            detalle["anio_detectado"] = datos["anio"]
            detalle["episodios_youtube"] = 0
            detalle["temporadas_disponibles"] = []

            series_por_nombre[clave_nombre] = detalle

        serie = series_por_nombre[clave_nombre]

        if video["video_id"] in ids_video_procesados:
            continue

        ids_video_procesados.add(video["video_id"])

        episodio = {
            "id": video["video_id"],
            "serie_id": serie["id"],
            "tmdb_id": serie["tmdb_id"],
            "serie": serie["titulo"],
            "temporada": datos["temporada"],
            "episodio": datos["episodio"],
            "titulo": (
                datos["titulo_episodio"]
                or f"Episodio {datos['episodio']}"
            ),
            "titulo_youtube": video["youtube_title"],
            "youtube_url": video["youtube_url"],
            "video_id": video["video_id"],
            "fecha_publicacion": video["published_at"],
        }

        episodios.append(episodio)
        serie["episodios_youtube"] += 1

        if datos["temporada"] not in serie["temporadas_disponibles"]:
            serie["temporadas_disponibles"].append(
                datos["temporada"]
            )

    # Ordenar temporadas y episodios.
    for serie in series_por_nombre.values():
        serie["temporadas_disponibles"].sort()

    episodios.sort(
        key=lambda ep: (
            normalizar(ep["serie"]),
            ep["temporada"],
            ep["episodio"],
            ep["titulo"].casefold(),
        )
    )

    lista_series = sorted(
        series_por_nombre.values(),
        key=lambda s: normalizar(s["titulo"])
    )

    series_json = {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "total_series": len(lista_series),
        "series": lista_series,
    }

    episodios_json = {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "total_episodios": len(episodios),
        "episodios": episodios,
    }

    guardar_json(OUTPUT_SERIES, series_json)
    guardar_json(OUTPUT_EPISODES, episodios_json)

    print("------------------------------------------")
    print(f"Series guardadas: {len(lista_series)}")
    print(f"Episodios guardados: {len(episodios)}")
    print(f"Archivo generado: {OUTPUT_SERIES}")
    print(f"Archivo generado: {OUTPUT_EPISODES}")
    print("Actualización terminada.")


if __name__ == "__main__":
    main()
