
import os
import re
import json
import time
import unicodedata
from datetime import datetime, timezone

import requests

# =====================================================
# CONFIGURACIÓN
# =====================================================

YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
TMDB_KEY = os.getenv("TMDB_API_KEY", "").strip()

PLAYLIST_ID = os.getenv(
    "YOUTUBE_SERIES_PLAYLIST_ID",
    "PLAeNuSpPHAsk"
).strip()

YOUTUBE_API = "https://www.googleapis.com/youtube/v3/playlistItems"
TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGES = "https://image.tmdb.org/t/p/"

if not YOUTUBE_KEY:
    raise SystemExit("ERROR: falta el secreto YOUTUBE_API_KEY.")

if not TMDB_KEY:
    raise SystemExit("ERROR: falta el secreto TMDB_API_KEY.")

if not PLAYLIST_ID:
    raise SystemExit("ERROR: falta el ID de la lista de YouTube.")

session = requests.Session()


# =====================================================
# UTILIDADES
# =====================================================

def get_json(url, params):
    response = session.get(url, params=params, timeout=40)

    if not response.ok:
        raise RuntimeError(
            f"Error HTTP {response.status} al consultar {url}: "
            f"{response.text[:400]}"
        )

    return response.json()


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(
        c for c in texto if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", texto).strip().casefold()


def guardar_json(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def imagen_tmdb(path, size="w500"):
    if not path:
        return None
    return f"{TMDB_IMAGES}{size}{path}"


# =====================================================
# YOUTUBE: LEER TODAS LAS PÁGINAS DE LA LISTA
# =====================================================

def youtube_videos():
    videos = []
    vistos = set()
    page_token = None

    while True:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": PLAYLIST_ID,
            "maxResults": 50,
            "key": YOUTUBE_KEY,
        }

        if page_token:
            params["pageToken"] = page_token

        data = get_json(YOUTUBE_API, params)

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            details = item.get("contentDetails", {})
            video_id = details.get("videoId")

            if not video_id or video_id in vistos:
                continue

            vistos.add(video_id)
            titulo = (snippet.get("title") or "").strip()

            if not titulo:
                continue

            if titulo.lower() in {
                "deleted video", "private video",
                "vídeo eliminado", "video eliminado",
                "vídeo privado", "video privado"
            }:
                continue

            videos.append({
                "video_id": video_id,
                "youtube_url": (
                    f"https://www.youtube.com/watch?v={video_id}"
                ),
                "youtube_title": titulo,
                "published_at": (
                    details.get("videoPublishedAt")
                    or snippet.get("publishedAt")
                ),
            })

        page_token = data.get("nextPageToken")

        if not page_token:
            break

    return videos


# =====================================================
# DETECTAR TÍTULOS COMO:
# Los dinosaurios (2026) S01E01 - Ascenso
# =====================================================

EPISODE_PATTERN = re.compile(
    r"^(?P<serie>.+?)\s+"
    r"(?:\((?P<year>\d{4})\)\s*)?"
    r"S(?P<season>\d{1,2})E(?P<episode>\d{1,3})"
    r"(?:\s*[-–—:]\s*(?P<title>.*))?$",
    re.IGNORECASE
)


def analizar_titulo(titulo):
    titulo = re.sub(r"\s+", " ", titulo).strip()
    match = EPISODE_PATTERN.match(titulo)

    if not match:
        return None

    nombre = re.sub(
        r"[\s\-–—:]+$",
        "",
        match.group("serie")
    ).strip()

    if not nombre:
        return None

    return {
        "serie": nombre,
        "anio": int(match.group("year"))
        if match.group("year") else None,
        "temporada": int(match.group("season")),
        "episodio": int(match.group("episode")),
        "titulo_episodio": (match.group("title") or "").strip(),
    }


# =====================================================
# TMDB
# =====================================================

def tmdb_get(endpoint, params=None):
    params = dict(params or {})
    params["api_key"] = TMDB_KEY
    params.setdefault("language", "es-ES")

    result = get_json(TMDB_API + endpoint, params)
    time.sleep(0.2)
    return result


def buscar_serie(nombre, anio=None):
    params = {
        "query": nombre,
        "include_adult": "false",
    }

    if anio:
        params["first_air_date_year"] = anio

    data = tmdb_get("/search/tv", params)
    results = data.get("results", [])

    if not results and anio:
        data = tmdb_get("/search/tv", {
            "query": nombre,
            "include_adult": "false",
        })
        results = data.get("results", [])

    if not results:
        print(f"AVISO: TMDB no encontró la serie: {nombre}")
        return None

    objetivo = normalizar(nombre)

    exactos = [
        item for item in results
        if objetivo in (
            normalizar(item.get("name")),
            normalizar(item.get("original_name"))
        )
    ]

    if exactos:
        if anio:
            por_anio = [
                item for item in exactos
                if (item.get("first_air_date") or "").startswith(
                    str(anio)
                )
            ]
            if por_anio:
                return por_anio[0]
        return exactos[0]

    if anio:
        por_anio = [
            item for item in results
            if (item.get("first_air_date") or "").startswith(
                str(anio)
            )
        ]
        if por_anio:
            return por_anio[0]

    return results[0]


def detalle_serie(resultado):
    data = tmdb_get(f"/tv/{resultado['id']}")

    return {
        "id": f"tmdb-{data['id']}",
        "tmdb_id": data["id"],
        "title": data.get("name") or resultado.get("name") or "",
        "name": data.get("name") or resultado.get("name") or "",
        "original_name": data.get("original_name") or "",
        "overview": data.get("overview") or "",
        "poster": imagen_tmdb(data.get("poster_path"), "w500"),
        "backdrop": imagen_tmdb(data.get("backdrop_path"), "original"),
        "first_air_date": data.get("first_air_date") or "",
        "genres": [
            g.get("name") for g in data.get("genres", [])
            if g.get("name")
        ],
        "vote_average": data.get("vote_average"),
        "logo": None,
        "temporadas_disponibles": [],
        "total_episodios_youtube": 0,
    }


# =====================================================
# GENERAR SERIES.JSON Y EPISODIOS.JSON
# Compatible con la estructura usada por series.html
# =====================================================

def main():
    print("Actualizando catálogo de series SharkTV...")
    print(f"Lista de YouTube: {PLAYLIST_ID}")

    videos = youtube_videos()
    print(f"Vídeos encontrados: {len(videos)}")

    series_por_nombre = {}
    episodios_por_serie = {}
    videos_procesados = set()

    for video in videos:
        datos = analizar_titulo(video["youtube_title"])

        if not datos:
            print(
                "AVISO: no se reconoce el título: "
                + video["youtube_title"]
            )
            continue

        clave = normalizar(datos["serie"])

        if clave not in series_por_nombre:
            resultado = buscar_serie(
                datos["serie"],
                datos["anio"]
            )

            if resultado:
                serie = detalle_serie(resultado)
            else:
                slug = re.sub(
                    r"[^a-z0-9]+", "-", clave
                ).strip("-")

                serie = {
                    "id": f"local-{slug}",
                    "tmdb_id": None,
                    "title": datos["serie"],
                    "name": datos["serie"],
                    "original_name": datos["serie"],
                    "overview": "",
                    "poster": None,
                    "backdrop": None,
                    "first_air_date": (
                        str(datos["anio"]) if datos["anio"] else ""
                    ),
                    "genres": [],
                    "vote_average": None,
                    "logo": None,
                    "temporadas_disponibles": [],
                    "total_episodios_youtube": 0,
                }

            series_por_nombre[clave] = serie
            episodios_por_serie[serie["id"]] = {}

        serie = series_por_nombre[clave]
        serie_id = serie["id"]
        video_id = video["video_id"]

        if video_id in videos_procesados:
            continue

        videos_procesados.add(video_id)

        num_temp = datos["temporada"]
        num_ep = datos["episodio"]

        temporadas = episodios_por_serie[serie_id]

        if num_temp not in temporadas:
            temporadas[num_temp] = {
                "numero": num_temp,
                "capitulos": [],
            }

        temporadas[num_temp]["capitulos"].append({
            "numero": num_ep,
            "titulo": (
                datos["titulo_episodio"]
                or f"Episodio {num_ep}"
            ),
            "imagen": (
                f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
            ),
            "descripcion": "",
            "url": video["youtube_url"],
            "video_id": video_id,
            "titulo_youtube": video["youtube_title"],
        })

        serie["total_episodios_youtube"] += 1

        if num_temp not in serie["temporadas_disponibles"]:
            serie["temporadas_disponibles"].append(num_temp)

    series = list(series_por_nombre.values())

    for serie in series:
        serie["temporadas_disponibles"].sort()

    series.sort(key=lambda s: normalizar(s["title"]))

    # El HTML espera un ARRAY directo en series.json.
    guardar_json("series.json", series)

    # El HTML espera episodios agrupados por ID de serie.
    episodios_json = {}

    for serie_id, temporadas in episodios_por_serie.items():
        ordenadas = []

        for temporada in sorted(temporadas):
            capitulos = temporadas[temporada]["capitulos"]
            capitulos.sort(key=lambda ep: ep["numero"])
            ordenadas.append(temporadas[temporada])

        episodios_json[serie_id] = ordenadas

    guardar_json("episodios.json", episodios_json)

    print(f"Series generadas: {len(series)}")
    print(
        "Episodios generados: "
        + str(sum(
            len(t["capitulos"])
            for temporadas in episodios_por_serie.values()
            for t in temporadas
        ))
    )
    print("Actualización completada.")


if __name__ == "__main__":
    main()
