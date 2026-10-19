
import os
import re
import json
import time
import unicodedata
from pathlib import Path

import requests

# ==========================================================
# CONFIGURACIÓN SHARKTV
# ==========================================================

ROOT = Path(__file__).resolve().parent

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
PLAYLIST_ID = os.getenv(
    "YOUTUBE_SERIES_PLAYLIST_ID",
    "PLAeNuSpPHAsk"
).strip()

YT = "https://www.googleapis.com/youtube/v3"
TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"

# Ejemplos aceptados:
# Los dinosaurios (2026) S01E01 - Ascenso
# Mi Serie S1E2 - El misterio
PATTERN = re.compile(
    r"^(?P<name>.+?)\s+S(?P<season>\d{1,2})"
    r"\s*E(?P<episode>\d{1,3})\b"
    r"(?:\s*[-–—:|]\s*(?P<epname>.*))?$",
    re.IGNORECASE
)


# ==========================================================
# FUNCIONES GENERALES
# ==========================================================

def norm(texto):
    texto = unicodedata.normalize(
        "NFKD", str(texto or "")
    ).encode("ascii", "ignore").decode("ascii")

    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def get_json(url, params=None):
    respuesta = requests.get(
        url,
        params=params,
        timeout=30
    )
    respuesta.raise_for_status()
    return respuesta.json()


def image_url(path, size="w780"):
    if not path:
        return ""
    return IMG + size + path


def leer_json_anterior(nombre, valor_por_defecto):
    ruta = ROOT / nombre

    try:
        with ruta.open("r", encoding="utf-8") as archivo:
            return json.load(archivo)
    except (OSError, json.JSONDecodeError):
        return valor_por_defecto


def guardar_json(nombre, datos):
    ruta = ROOT / nombre

    with ruta.open("w", encoding="utf-8") as archivo:
        json.dump(
            datos,
            archivo,
            ensure_ascii=False,
            indent=2
        )

    print("Archivo actualizado:", nombre)


# ==========================================================
# YOUTUBE
# ==========================================================

def youtube_items():
    elementos = []
    token = None

    while True:
        parametros = {
            "part": "snippet,contentDetails",
            "playlistId": PLAYLIST_ID,
            "maxResults": 50,
            "key": YOUTUBE_API_KEY
        }

        if token:
            parametros["pageToken"] = token

        datos = get_json(
            YT + "/playlistItems",
            parametros
        )

        elementos.extend(datos.get("items", []))
        token = datos.get("nextPageToken")

        if not token:
            break

        time.sleep(0.1)

    return elementos


# ==========================================================
# TMDB
# ==========================================================

def tmdb_search(nombre, cache):
    clave = norm(nombre)

    if clave in cache:
        return cache[clave]

    parametros = {
        "api_key": TMDB_API_KEY,
        "language": "es-ES",
        "query": nombre,
        "include_adult": "false"
    }

    datos = get_json(
        TMDB + "/search/tv",
        parametros
    )

    resultados = datos.get("results", [])

    if not resultados:
        parametros["language"] = "en-US"

        datos = get_json(
            TMDB + "/search/tv",
            parametros
        )

        resultados = datos.get("results", [])

    elegido = None
    nombre_normalizado = norm(nombre)

    for elemento in resultados:
        if (
            norm(elemento.get("name")) == nombre_normalizado
            or norm(elemento.get("original_name")) == nombre_normalizado
        ):
            elegido = elemento
            break

    if not elegido and resultados:
        elegido = resultados[0]

    cache[clave] = elegido
    return elegido


def contar_episodios(datos):
    """
    Cuenta capítulos de forma segura.
    Admite listas, diccionarios y estructuras anidadas.
    Ignora valores numéricos que no sean capítulos.
    """

    if isinstance(datos, list):
        return sum(contar_episodios(elemento) for elemento in datos)

    if isinstance(datos, dict):
        capitulos = datos.get("capitulos")

        if isinstance(capitulos, list):
            return len(capitulos)

        total = 0

        for clave, valor in datos.items():
            if clave == "capitulos":
                continue

            total += contar_episodios(valor)

        return total

    return 0


# ==========================================================
# PROCESO PRINCIPAL
# ==========================================================

def main():
    print("Actualizando catálogo de series SharkTV...")
    print("Lista de YouTube:", PLAYLIST_ID)

    if not YOUTUBE_API_KEY:
        raise SystemExit(
            "ERROR: falta el secreto YOUTUBE_API_KEY en GitHub."
        )

    if not TMDB_API_KEY:
        raise SystemExit(
            "ERROR: falta el secreto TMDB_API_KEY en GitHub."
        )

    if not PLAYLIST_ID:
        raise SystemExit(
            "ERROR: falta el ID de la lista de reproducción."
        )

    # Recuperar el catálogo anterior para reutilizar búsquedas.
    series_anteriores = leer_json_anterior("series.json", [])

    # Admitir también un catálogo anterior guardado como objeto.
    if isinstance(series_anteriores, dict):
        series_anteriores = series_anteriores.get("series", [])

    if not isinstance(series_anteriores, list):
        series_anteriores = []

    cache = {}

    for serie in series_anteriores:
        if (
            isinstance(serie, dict)
            and serie.get("title")
            and serie.get("tmdb_id")
        ):
            cache[norm(serie["title"])] = {
                "id": serie["tmdb_id"],
                "name": serie["title"],
                "_cached": True
            }

    # Leer los vídeos de la lista de YouTube.
    items = youtube_items()
    print("Vídeos encontrados:", len(items))

    grupos = {}
    videos_vistos = set()

    for item in items:
        snippet = item.get("snippet", {})
        detalles = item.get("contentDetails") or {}

        recurso = snippet.get("resourceId") or {}
        video_id = detalles.get("videoId") or recurso.get("videoId")

        if not video_id or video_id in videos_vistos:
            continue

        videos_vistos.add(video_id)

        titulo_original = (snippet.get("title") or "").strip()

        if titulo_original in ("Deleted video", "Private video"):
            continue

        coincidencia = PATTERN.match(titulo_original)

        if not coincidencia:
            print(
                "Se omite (falta S01E01 en el título):",
                titulo_original
            )
            continue

        nombre = coincidencia.group("name").strip(" -–—:|")
        temporada = int(coincidencia.group("season"))
        numero_episodio = int(coincidencia.group("episode"))

        clave = norm(nombre)

        miniaturas = snippet.get("thumbnails") or {}
        miniatura = (
            miniaturas.get("high")
            or miniaturas.get("medium")
            or miniaturas.get("default")
            or {}
        ).get("url", "")

        video = {
            "video_id": video_id,
            "youtube_title": titulo_original,
            "episode_title": (
                coincidencia.group("epname") or ""
            ).strip(),
            "season": temporada,
            "episode": numero_episodio,
            "url": "https://www.youtube.com/watch?v=" + video_id,
            "youtube_thumbnail": miniatura
        }

        if clave not in grupos:
            grupos[clave] = {
                "name": nombre,
                "videos": []
            }

        grupos[clave]["videos"].append(video)

    # Construir los dos JSON que utiliza la web.
    series_out = []
    episodes_out = {}

    for clave, grupo in grupos.items():
        nombre = grupo["name"]
        videos = grupo["videos"]

        print("Procesando serie:", nombre)

        encontrado = tmdb_search(nombre, cache)
        tmdb_id = encontrado.get("id") if encontrado else None

        if tmdb_id:
            series_id = str(tmdb_id)
        else:
            slug = re.sub(
                r"[^a-z0-9]+",
                "-",
                norm(nombre)
            ).strip("-")

            series_id = "local-" + slug

        detalle = encontrado or {}

        # Obtener detalles, imágenes, géneros y logo de TMDB.
        if tmdb_id:
            try:
                detalle = get_json(
                    f"{TMDB}/tv/{tmdb_id}",
                    {
                        "api_key": TMDB_API_KEY,
                        "language": "es-ES",
                        "append_to_response": "images"
                    }
                )
            except Exception as error:
                print(
                    "Aviso: no se pudieron cargar los detalles TMDB:",
                    nombre,
                    error
                )
                detalle = encontrado or {}

        poster = image_url(
            detalle.get("poster_path"),
            "w780"
        )

        backdrop = image_url(
            detalle.get("backdrop_path"),
            "original"
        )

        logo = ""

        imagenes = detalle.get("images") or {}
        logos = imagenes.get("logos") or []

        if logos:
            logos.sort(
                key=lambda elemento: (
                    0 if elemento.get("iso_639_1") in ("es", "en", None) else 1,
                    -(elemento.get("vote_average") or 0)
                )
            )

            logo = image_url(
                logos[0].get("file_path"),
                "w780"
            )

        generos = [
            genero.get("name", "")
            for genero in (detalle.get("genres") or [])
            if genero.get("name")
        ]

        series_out.append({
            "id": series_id,
            "tmdb_id": tmdb_id,
            "type": "series",
            "title": (
                detalle.get("name")
                or detalle.get("original_name")
                or nombre
            ),
            "poster": poster or videos[0]["youtube_thumbnail"],
            "backdrop": backdrop,
            "logo": logo,
            "overview": detalle.get("overview") or "",
            "first_air_date": detalle.get("first_air_date") or "",
            "genres": ", ".join(generos),
            "vote_average": detalle.get("vote_average") or 0,
            "youtube_title_example": videos[0]["youtube_title"]
        })

        # Agrupar los vídeos por temporada y episodio.
        temporadas = {}

        for video in videos:
            numero_temporada = video["season"]
            numero_episodio = video["episode"]

            temporadas.setdefault(numero_temporada, {})

            temporadas[numero_temporada][numero_episodio] = {
                "numero": numero_episodio,
                "titulo": (
                    video["episode_title"]
                    or f"Capítulo {numero_episodio}"
                ),
                "imagen": video["youtube_thumbnail"],
                "url": video["url"],
                "youtube_id": video["video_id"],
                "youtube_title": video["youtube_title"]
            }

        # Completar los datos de los episodios con TMDB.
        if tmdb_id:
            for numero_temporada in sorted(temporadas):
                try:
                    datos_temporada = get_json(
                        f"{TMDB}/tv/{tmdb_id}/season/{numero_temporada}",
                        {
                            "api_key": TMDB_API_KEY,
                            "language": "es-ES"
                        }
                    )

                    episodios_tmdb = {
                        int(episodio["episode_number"]): episodio
                        for episodio in datos_temporada.get("episodes", [])
                        if episodio.get("episode_number") is not None
                    }

                    for numero_episodio, episodio in temporadas[
                        numero_temporada
                    ].items():
                        episodio_tmdb = episodios_tmdb.get(numero_episodio)

                        if episodio_tmdb:
                            episodio["titulo"] = (
                                episodio_tmdb.get("name")
                                or episodio["titulo"]
                            )

                            episodio["descripcion"] = (
                                episodio_tmdb.get("overview") or ""
                            )

                            episodio["imagen"] = (
                                image_url(
                                    episodio_tmdb.get("still_path"),
                                    "w780"
                                )
                                or episodio["imagen"]
                            )

                except Exception as error:
                    print(
                        f"Aviso: no se pudieron consultar los episodios "
                        f"de la temporada {numero_temporada} de {nombre}: "
                        f"{error}"
                    )

        # Formato que utiliza series.html:
        # [{ "numero": 1, "capitulos": [...] }]
        temporadas_out = []

        for numero_temporada in sorted(temporadas):
            capitulos = [
                temporadas[numero_temporada][numero]
                for numero in sorted(temporadas[numero_temporada])
            ]

            temporadas_out.append({
                "numero": numero_temporada,
                "capitulos": capitulos
            })

        episodes_out[series_id] = temporadas_out

    # Orden alfabético de las series.
    series_out.sort(
        key=lambda serie: serie["title"].casefold()
    )

    # Guardar archivos como JSON directamente, sin envoltorios,
    # para mantener la compatibilidad con la plantilla HTML.
    guardar_json("series.json", series_out)
    guardar_json("episodios.json", episodes_out)

    total_capitulos = contar_episodios(episodes_out)

    print("----------------------------------------")
    print("Actualización terminada correctamente.")
    print("Series generadas:", len(series_out))
    print("Capítulos encontrados:", total_capitulos)
    print("----------------------------------------")


if __name__ == "__main__":
    main()

