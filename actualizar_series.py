
import os
import re
import json
import time
import unicodedata
import requests
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ============================================================
# SHARKTV - ACTUALIZADOR AUTOMATICO DE SERIES Y EPISODIOS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
PLAYLIST_ID = os.getenv(
    "YOUTUBE_SERIES_PLAYLIST_ID",
    "PLAeNuSpPHAsk"
).strip()

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGES = "https://image.tmdb.org/t/p"

ARCHIVO_SERIES = BASE_DIR / "series.json"
ARCHIVO_EPISODIOS = BASE_DIR / "episodios.json"

IDIOMA = "es-ES"
TIMEOUT = 25

session = requests.Session()
session.headers.update({"User-Agent": "SharkTV-Series-Updater/2.0"})

# Títulos alternativos conocidos. Sirven cuando el título de YouTube
# está en español y TMDB encuentra la serie con su nombre original.
ALIAS_TITULOS = {
    "los dinosaurios": ["Los dinosaurios", "The Dinosaurs"],
}


# ============================================================
# UTILIDADES
# ============================================================

def leer_json(ruta, valor_por_defecto):
    try:
        if ruta.exists():
            with ruta.open("r", encoding="utf-8") as archivo:
                return json.load(archivo)
    except (OSError, json.JSONDecodeError) as error:
        print(f"AVISO: no se pudo leer {ruta.name}: {error}")

    return valor_por_defecto


def guardar_json(ruta, datos):
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")

    with temporal.open("w", encoding="utf-8") as archivo:
        json.dump(
            datos,
            archivo,
            ensure_ascii=False,
            indent=2
        )
        archivo.write("\n")

    temporal.replace(ruta)


def normalizar(texto):
    """Normaliza títulos conservando letras y números de cualquier idioma."""
    texto = str(texto or "").lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        caracter for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )
    # isalnum() conserva caracteres coreanos, japoneses, chinos, cirílicos,
    # árabes, etc.; no limitar a [a-z0-9], que destruye esos títulos.
    texto = "".join(caracter if caracter.isalnum() else " " for caracter in texto)
    return re.sub(r"\s+", " ", texto).strip()


def crear_slug(texto):
    texto = normalizar(texto).replace(" ", "-")
    return texto or "serie-sin-titulo"


def url_imagen(ruta, tamano="original"):
    if not ruta:
        return ""
    return f"{TMDB_IMAGES}/{tamano}{ruta}"


def peticion_json(url, params=None, intentos=3):
    for intento in range(intentos):
        try:
            respuesta = session.get(
                url,
                params=params,
                timeout=TIMEOUT
            )

            if respuesta.status_code == 429:
                # No dejar que una respuesta del servidor bloquee el workflow
                # durante minutos: reintenta con una espera limitada.
                try:
                    espera = int(respuesta.headers.get("Retry-After", "3"))
                except (TypeError, ValueError):
                    espera = 3
                espera = min(max(espera, 2), 8)
                print(f"Límite de solicitudes (429). Reintento en {espera} s...")
                if intento < intentos - 1:
                    time.sleep(espera)
                    continue
                return {}

            respuesta.raise_for_status()
            return respuesta.json()

        except (requests.RequestException, ValueError) as error:
            print(f"Error consultando {url}: {error}")

            if intento < intentos - 1:
                time.sleep(2)

    return {}


# ============================================================
# YOUTUBE
# ============================================================

def extraer_video_id(item):
    contenido = item.get("contentDetails", {})
    video_id = contenido.get("videoId")

    if video_id:
        return video_id

    snippet = item.get("snippet", {})
    recurso = snippet.get("resourceId", {})
    video_id = recurso.get("videoId")

    if video_id:
        return video_id

    enlace = (
        item.get("url")
        or item.get("link")
        or snippet.get("url")
        or ""
    )

    if "youtu.be/" in enlace:
        return enlace.split("youtu.be/")[-1].split("?")[0]

    if "youtube.com" in enlace:
        return parse_qs(urlparse(enlace).query).get("v", [""])[0]

    return ""


def detectar_calidad(descripcion):
    """Detecta calidad declarada en la descripción del vídeo de YouTube."""
    texto = str(descripcion or "").upper()
    # Prioriza el formato explícito "Calidad: 4K/FHD/HD".
    coincidencia = re.search(
        r"(?:CALIDAD|QUALITY)\s*[:=\-]\s*(4K|UHD|2160P|FHD|FULL\s*HD|1080P|HD|720P)",
        texto,
        re.IGNORECASE,
    )
    valor = coincidencia.group(1).upper().replace(" ", "") if coincidencia else ""
    if valor in ("4K", "UHD", "2160P"):
        return "4K"
    if valor in ("FHD", "FULLHD", "1080P"):
        return "FHD"
    if valor in ("HD", "720P"):
        return "HD"

    # También admite una resolución escrita sin etiqueta, si se desea.
    if re.search(r"\b(2160P|UHD|4K)\b", texto):
        return "4K"
    if re.search(r"\b(1080P|FHD|FULL\s*HD)\b", texto):
        return "FHD"
    if re.search(r"\b(720P|HD)\b", texto):
        return "HD"
    return ""


def completar_descripciones_videos(videos):
    """Obtiene las descripciones completas por lotes usando videos.list."""
    for inicio in range(0, len(videos), 50):
        lote = videos[inicio:inicio + 50]
        ids = [v["youtube_id"] for v in lote if v.get("youtube_id")]
        if not ids:
            continue
        datos = peticion_json(
            f"{YOUTUBE_API}/videos",
            {"part": "snippet", "id": ",".join(ids), "key": YOUTUBE_API_KEY},
        )
        por_id = {
            item.get("id"): item.get("snippet", {}).get("description", "")
            for item in datos.get("items", [])
        }
        for video in lote:
            video["youtube_description"] = por_id.get(video["youtube_id"], "")
            video["quality_detected"] = detectar_calidad(video["youtube_description"])
    return videos


def obtener_videos_playlist():
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "Falta el secreto YOUTUBE_API_KEY en GitHub."
        )

    if not PLAYLIST_ID:
        raise RuntimeError(
            "Falta YOUTUBE_SERIES_PLAYLIST_ID."
        )

    videos = []
    pagina = None

    while True:
        parametros = {
            "part": "snippet,contentDetails",
            "playlistId": PLAYLIST_ID,
            "maxResults": 50,
            "key": YOUTUBE_API_KEY,
        }

        if pagina:
            parametros["pageToken"] = pagina

        datos = peticion_json(
            f"{YOUTUBE_API}/playlistItems",
            parametros
        )

        if "error" in datos:
            raise RuntimeError(
                "YouTube ha devuelto un error: "
                + str(datos["error"])
            )

        elementos = datos.get("items", [])

        for item in elementos:
            snippet = item.get("snippet", {})
            video_id = extraer_video_id(item)
            titulo = (snippet.get("title") or "").strip()

            if not video_id or not titulo:
                continue

            if titulo.lower() in (
                "deleted video",
                "private video"
            ):
                continue

            videos.append({
                "youtube_id": video_id,
                "youtube_title": titulo,
                "youtube_url": (
                    f"https://www.youtube.com/watch?v={video_id}"
                ),
                "youtube_image": (
                    f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                ),
                "youtube_description": (snippet.get("description") or ""),
                "quality_detected": detectar_calidad(snippet.get("description") or ""),
            })

        pagina = datos.get("nextPageToken")

        if not pagina:
            break

    # Evita duplicar un vídeo si aparece repetido en la lista.
    unicos = {}
    for video in videos:
        unicos[video["youtube_id"]] = video

    resultado = list(unicos.values())
    resultado = completar_descripciones_videos(resultado)
    print(f"YouTube: {len(resultado)} vídeos encontrados.")
    calidades = sum(1 for v in resultado if v.get("quality_detected"))
    print(f"Calidad detectada en descripciones: {calidades} vídeos.")
    return resultado


# ============================================================
# ANALIZAR TITULOS DE YOUTUBE
# Formato esperado: Nombre de serie S01E01 - Nombre del episodio
# También acepta S01E1, 1x01, etc.
# ============================================================

PATRON_EPISODIO = re.compile(
    r"^(?P<serie>.*?)\s+"
    r"(?:S(?P<temporada>\d{1,2})\s*E(?P<episodio>\d{1,3})"
    r"|(?P<temporada2>\d{1,2})x(?P<episodio2>\d{1,3}))"
    r"(?:\s*[-–—:|]\s*(?P<titulo>.*))?$",
    re.IGNORECASE
)


def analizar_titulo(titulo):
    titulo = (titulo or "").strip()
    coincidencia = PATRON_EPISODIO.match(titulo)

    if not coincidencia:
        return None

    temporada = (
        coincidencia.group("temporada")
        or coincidencia.group("temporada2")
    )

    numero_episodio = (
        coincidencia.group("episodio")
        or coincidencia.group("episodio2")
    )

    nombre_serie = coincidencia.group("serie").strip()
    nombre_episodio = (
        coincidencia.group("titulo") or ""
    ).strip()

    # Elimina espacios y separadores sobrantes del título de la serie.
    nombre_serie = re.sub(r"[\s\-–—:|]+$", "", nombre_serie).strip()

    if not nombre_serie:
        return None

    return {
        "serie": nombre_serie,
        "temporada": int(temporada),
        "episodio": int(numero_episodio),
        "titulo_episodio": nombre_episodio,
    }


def separar_titulo_y_anio(nombre):
    nombre = (nombre or "").strip()
    coincidencia = re.search(r"\s*\(((?:19|20)\d{2})\)\s*$", nombre)

    if not coincidencia:
        return nombre, None

    anio = int(coincidencia.group(1))
    nombre_base = nombre[:coincidencia.start()].strip()
    return nombre_base, anio


# ============================================================
# TMDB: BUSQUEDA INTELIGENTE
# ============================================================

def buscar_series_tmdb(consulta, anio=None, idioma=IDIOMA):
    """Busca series; el año se usa como pista, no como filtro obligatorio."""
    resultados = []
    consultas = []
    if anio:
        consultas.append((consulta, anio))
    consultas.append((consulta, None))

    vistos = set()
    for texto, anio_busqueda in consultas:
        parametros = {
            "api_key": TMDB_API_KEY,
            "query": texto,
            "language": idioma,
            "include_adult": "false",
        }
        if anio_busqueda:
            parametros["first_air_date_year"] = anio_busqueda

        datos = peticion_json(f"{TMDB_API}/search/tv", parametros)
        for candidato in datos.get("results", []):
            candidato_id = candidato.get("id")
            if candidato_id and candidato_id not in vistos:
                vistos.add(candidato_id)
                resultados.append(candidato)
        time.sleep(0.05)
    return resultados


def puntuar_candidato(candidato, nombre_base, anio):
    nombre = candidato.get("name", "")
    original = candidato.get("original_name", "")
    fecha = candidato.get("first_air_date", "")
    buscado = normalizar(nombre_base)
    nombres = [normalizar(nombre), normalizar(original)]

    # Similaridad tolerante para títulos traducidos o con pequeñas diferencias.
    from difflib import SequenceMatcher
    similitud = max(
        (SequenceMatcher(None, buscado, n).ratio() for n in nombres if buscado and n),
        default=0.0,
    )
    puntuacion = int(similitud * 70)

    for n in nombres:
        if buscado and n == buscado:
            puntuacion = max(puntuacion, 100)
        elif buscado and (buscado in n or n in buscado):
            puntuacion = max(puntuacion, 65)

    if anio and fecha:
        try:
            anio_tmdb = int(fecha[:4])
            if anio_tmdb == anio:
                puntuacion += 25
            else:
                # Un año incorrecto no elimina la posibilidad de coincidencia.
                puntuacion -= min(abs(anio_tmdb - anio) * 3, 15)
        except (ValueError, TypeError):
            pass

    if candidato.get("poster_path"):
        puntuacion += 2
    if candidato.get("backdrop_path"):
        puntuacion += 2
    if candidato.get("overview"):
        puntuacion += 1
    return puntuacion


def buscar_serie_tmdb(nombre_youtube):
    nombre_base, anio = separar_titulo_y_anio(nombre_youtube)

    # Un nombre puede venir como: Título español (Original) (2025).
    # Probamos cada variante por separado y con/sin año.
    sin_anio = re.sub(r"\s*\(\s*(?:19|20)\d{2}\s*\)\s*$", "", nombre_base).strip()
    grupos = re.findall(r"\(([^()]*)\)", sin_anio)
    titulo_principal = re.sub(r"\([^()]*\)", " ", sin_anio)
    titulo_principal = re.sub(r"\s+", " ", titulo_principal).strip(" -|,;")

    nombres_busqueda = []
    if titulo_principal:
        nombres_busqueda.append(titulo_principal)
    for grupo in grupos:
        parte = re.sub(r"\b(?:19|20)\d{2}\b", " ", grupo)
        parte = re.sub(r"\s+", " ", parte).strip(" -|,;")
        if parte:
            nombres_busqueda.append(parte)
    if sin_anio:
        nombres_busqueda.append(sin_anio)
    if not nombres_busqueda:
        nombres_busqueda.append(nombre_base)

    clave = normalizar(titulo_principal or sin_anio)
    for clave_alias, alias in ALIAS_TITULOS.items():
        if clave and clave == normalizar(clave_alias):
            nombres_busqueda.extend(alias)

    unicos = []
    vistos = set()
    for nombre in nombres_busqueda:
        llave = normalizar(nombre)
        if llave and llave not in vistos:
            vistos.add(llave)
            unicos.append(nombre)

    candidatos_por_id = {}
    # TMDB devuelve traducciones diferentes según language. También se consulta
    # el título original sin exigir que sea inglés o español.
    for consulta in unicos:
        for idioma in (IDIOMA, "en-US", "ko-KR", "ja-JP", "zh-CN", "fr-FR", "de-DE"):
            for candidato in buscar_series_tmdb(consulta, anio=anio, idioma=idioma):
                candidato_id = candidato.get("id")
                if not candidato_id:
                    continue
                puntuacion = puntuar_candidato(candidato, consulta, anio)
                anterior = candidatos_por_id.get(candidato_id)
                if anterior is None or puntuacion > anterior.get("_puntuacion", -1):
                    candidato["_puntuacion"] = puntuacion
                    candidatos_por_id[candidato_id] = candidato

    if not candidatos_por_id:
        print(f"TMDB: no se encontró la serie '{nombre_youtube}'. Se conservará con ID local.")
        return None

    candidatos = sorted(candidatos_por_id.values(), key=lambda item: item.get("_puntuacion", 0), reverse=True)
    mejor = candidatos[0]
    puntuacion = mejor.get("_puntuacion", 0)
    print(
        f"TMDB candidato: {mejor.get('name')} / {mejor.get('original_name')} "
        f"({mejor.get('first_air_date', '')}) | ID {mejor.get('id')} | puntuación {puntuacion}"
    )

    # Umbral prudente para evitar enlazar una serie completamente distinta.
    if puntuacion < 35:
        print(f"TMDB: coincidencia poco segura para '{nombre_youtube}'. Se conserva ID local.")
        return None
    return mejor


def obtener_detalles_serie(tmdb_id):
    parametros = {
        "api_key": TMDB_API_KEY,
        "language": IDIOMA,
        "append_to_response": "images",
        "include_image_language": "es,en,null",
    }

    datos = peticion_json(
        f"{TMDB_API}/tv/{tmdb_id}",
        parametros
    )

    if not datos or not datos.get("id"):
        return {}

    imagenes = datos.get("images", {})

    # Selecciona primero logos en español, luego inglés y finalmente otros.
    logos = imagenes.get("logos", [])

    def clave_logo(logo):
        idioma = logo.get("iso_639_1")
        preferencia = {
            "es": 0,
            "en": 1,
            None: 2,
        }.get(idioma, 3)

        return (
            preferencia,
            -(logo.get("vote_average") or 0),
            -(logo.get("width") or 0)
        )

    logos = sorted(logos, key=clave_logo)
    logo_path = logos[0].get("file_path") if logos else ""

    # Usa el fondo de la ficha y, si falta, el de las imágenes adicionales.
    backdrop_path = datos.get("backdrop_path") or ""
    if not backdrop_path:
        fondos = imagenes.get("backdrops", [])
        if fondos:
            fondos.sort(
                key=lambda item: (
                    -(item.get("vote_average") or 0),
                    -(item.get("width") or 0)
                )
            )
            backdrop_path = fondos[0].get("file_path", "")

    poster_path = datos.get("poster_path") or ""
    if not poster_path:
        posters = imagenes.get("posters", [])
        if posters:
            posters.sort(
                key=lambda item: (
                    -(item.get("vote_average") or 0),
                    -(item.get("width") or 0)
                )
            )
            poster_path = posters[0].get("file_path", "")

    generos = [
        genero.get("name", "")
        for genero in datos.get("genres", [])
        if genero.get("name")
    ]

    return {
        "id": datos.get("id"),
        "title": datos.get("name") or datos.get("original_name") or "",
        "poster": url_imagen(poster_path, "w780"),
        "backdrop": url_imagen(backdrop_path, "original"),
        "logo": url_imagen(logo_path, "original"),
        "overview": datos.get("overview") or "",
        "first_air_date": datos.get("first_air_date") or "",
        "genres": ", ".join(generos),
        "vote_average": datos.get("vote_average") or 0,
        "number_of_seasons": datos.get("number_of_seasons") or 0,
    }


def obtener_clasificacion_edad(tmdb_id):
    datos = peticion_json(
        f"{TMDB_API}/tv/{tmdb_id}/content_ratings",
        {"api_key": TMDB_API_KEY}
    )

    resultados = datos.get("results", [])

    # Prioriza España; si no existe, utiliza Estados Unidos.
    for codigo in ("ES", "US"):
        for item in resultados:
            if item.get("iso_3166_1") == codigo:
                return item.get("rating") or ""

    for item in resultados:
        if item.get("rating"):
            return item["rating"]

    return ""


# ============================================================
# TEMPORADAS Y EPISODIOS
# ============================================================

def obtener_datos_temporada(tmdb_id, numero_temporada):
    datos = peticion_json(
        f"{TMDB_API}/tv/{tmdb_id}/season/{numero_temporada}",
        {
            "api_key": TMDB_API_KEY,
            "language": IDIOMA,
        }
    )

    episodios = {}

    for episodio in datos.get("episodes", []):
        numero = episodio.get("episode_number")

        if numero is None:
            continue

        episodios[int(numero)] = {
            "titulo": episodio.get("name") or "",
            "sinopsis": episodio.get("overview") or "",
            "imagen": url_imagen(
                episodio.get("still_path"),
                "w780"
            ),
            "fecha": episodio.get("air_date") or "",
        }

    return episodios


def construir_catalogo(videos, series_anteriores, episodios_anteriores):
    series_por_nombre = {}
    episodios_por_serie = {}
    # Caché: una sola petición a TMDB por serie/temporada, no una por episodio.
    temporadas_tmdb_cache = {}

    # Indexar las fichas anteriores para conservar IDs y ajustes manuales.
    anteriores_por_nombre = {}
    anteriores_por_id = {}

    for serie in series_anteriores:
        nombre = serie.get("title") or ""
        if nombre:
            anteriores_por_nombre[normalizar(
                separar_titulo_y_anio(nombre)[0]
            )] = serie

        if serie.get("id"):
            anteriores_por_id[str(serie["id"])] = serie

    # Agrupar los vídeos por serie, temporada y episodio.
    for video in videos:
        analisis = analizar_titulo(video["youtube_title"])

        if not analisis:
            print(
                "AVISO: título ignorado porque no tiene formato "
                f"S01E01 o 1x01: {video['youtube_title']}"
            )
            continue

        nombre_serie = analisis["serie"]
        nombre_base, anio = separar_titulo_y_anio(nombre_serie)
        clave_nombre = normalizar(nombre_base)

        if clave_nombre not in series_por_nombre:
            anterior = anteriores_por_nombre.get(clave_nombre)

            # Conserva el ID previo cuando existe, evitando cambiar la URL
            # que usa series.html?id=...
            tmdb_en_anterior = (
                anterior.get("tmdb_id") if anterior else None
            )

            ficha_tmdb = None
            detalles = {}

            if TMDB_API_KEY:
                if tmdb_en_anterior:
                    detalles = obtener_detalles_serie(tmdb_en_anterior)

                if not detalles:
                    ficha_tmdb = buscar_serie_tmdb(nombre_serie)

                    if ficha_tmdb:
                        detalles = obtener_detalles_serie(
                            ficha_tmdb["id"]
                        )

            tmdb_id = (
                detalles.get("id")
                or (ficha_tmdb or {}).get("id")
                or tmdb_en_anterior
            )

            if tmdb_id and not detalles:
                detalles = obtener_detalles_serie(tmdb_id)

            if tmdb_id:
                serie_id = str(tmdb_id)
            elif anterior and anterior.get("id"):
                serie_id = str(anterior["id"])
            else:
                serie_id = "local-" + crear_slug(nombre_serie)

            # Si el ID local anterior se está convirtiendo a un ID TMDB,
            # se trasladarán también los episodios que hubiera guardados.
            serie = {
                "id": serie_id,
                "tmdb_id": tmdb_id,
                "type": "series",
                "title": (
                    detalles.get("title")
                    or nombre_serie
                ),
                "poster": (
                    detalles.get("poster")
                    or (anterior or {}).get("poster")
                    or video["youtube_image"]
                ),
                "backdrop": (
                    detalles.get("backdrop")
                    or (anterior or {}).get("backdrop")
                    or ""
                ),
                "logo": (
                    detalles.get("logo")
                    or (anterior or {}).get("logo")
                    or ""
                ),
                "overview": (
                    detalles.get("overview")
                    or (anterior or {}).get("overview")
                    or ""
                ),
                "first_air_date": (
                    detalles.get("first_air_date")
                    or (anterior or {}).get("first_air_date")
                    or (str(anio) if anio else "")
                ),
                "genres": (
                    detalles.get("genres")
                    or (anterior or {}).get("genres")
                    or ""
                ),
                "vote_average": (
                    detalles.get("vote_average")
                    or (anterior or {}).get("vote_average")
                    or 0
                ),
                "age_rating": (
                    (anterior or {}).get("age_rating") or ""
                ),
                "quality": (
                    video.get("quality_detected")
                    or (anterior or {}).get("quality")
                    or ""
                ),
                "youtube_title_example": video["youtube_title"],
            }

            # Mantiene las imágenes y datos editados manualmente si existen.
            if anterior:
                for campo in (
                    "title_manual",
                    "poster_manual",
                    "backdrop_manual",
                    "logo_manual",
                    "overview_manual",
                    "quality_manual",
                    "age_rating_manual",
                    "youtube_url_manual",
                ):
                    if anterior.get(campo):
                        serie[campo] = anterior[campo]

            if tmdb_id and TMDB_API_KEY:
                clasificacion = obtener_clasificacion_edad(tmdb_id)
                if clasificacion:
                    serie["age_rating"] = clasificacion

            series_por_nombre[clave_nombre] = serie
            episodios_por_serie[serie_id] = {}

            # Recupera episodios previos si el ID de la serie ha cambiado.
            if anterior:
                id_anterior = str(anterior.get("id", ""))
                guardados = episodios_anteriores.get(id_anterior, [])

                for temporada_guardada in guardados:
                    numero_temp = temporada_guardada.get("numero")
                    if numero_temp is None:
                        continue

                    episodios_por_serie[serie_id][int(numero_temp)] = {
                        int(ep.get("numero", 0)): ep
                        for ep in temporada_guardada.get("capitulos", [])
                        if ep.get("numero") is not None
                    }

            print(
                f"Serie: {serie['title']} | "
                f"ID: {serie_id} | TMDB: {tmdb_id or 'sin coincidencia'}"
            )

        serie = series_por_nombre[clave_nombre]
        # La calidad declarada en la descripción de YouTube se actualiza automáticamente.
        # quality_manual, si existe, sigue permitiendo una corrección manual en la web.
        if video.get("quality_detected") and not serie.get("quality_manual"):
            serie["quality"] = video["quality_detected"]
        serie_id = serie["id"]

        temporada = analisis["temporada"]
        numero_ep = analisis["episodio"]

        if temporada not in episodios_por_serie[serie_id]:
            episodios_por_serie[serie_id][temporada] = {}

        episodio_anterior = (
            episodios_por_serie[serie_id][temporada].get(numero_ep, {})
        )

        # La información del título y del enlace de YouTube manda sobre TMDB.
        # Así puedes cambiar el vídeo sin depender del nombre del episodio.
        titulo_youtube = analisis["titulo_episodio"]
        titulo_tmdb = ""
        sinopsis_tmdb = ""
        imagen_tmdb = ""

        tmdb_id = serie.get("tmdb_id")
        if tmdb_id and TMDB_API_KEY:
            clave_temporada = (str(tmdb_id), int(temporada))
            if clave_temporada not in temporadas_tmdb_cache:
                print(
                    f"TMDB: consultando temporada {temporada} "
                    f"de la serie {serie.get('title', serie_id)}..."
                )
                temporadas_tmdb_cache[clave_temporada] = obtener_datos_temporada(
                    tmdb_id,
                    temporada
                )
            temporada_tmdb = temporadas_tmdb_cache[clave_temporada]
            info_ep = temporada_tmdb.get(numero_ep, {})
            titulo_tmdb = info_ep.get("titulo", "")
            sinopsis_tmdb = info_ep.get("sinopsis", "")
            imagen_tmdb = info_ep.get("imagen", "")

        # Si el nombre del episodio de YouTube está presente, se conserva.
        # Si no, utiliza el de TMDB y luego el anterior.
        titulo_final = (
            titulo_youtube
            or titulo_tmdb
            or episodio_anterior.get("titulo")
            or f"Episodio {numero_ep}"
        )

        episodio = {
            "numero": numero_ep,
            "titulo": titulo_final,
            "imagen": (
                video["youtube_image"]
                or imagen_tmdb
                or episodio_anterior.get("imagen", "")
            ),
            "url": video["youtube_url"],
            "youtube_id": video["youtube_id"],
            "youtube_title": video["youtube_title"],
            "sinopsis": (
                sinopsis_tmdb
                or episodio_anterior.get("sinopsis", "")
            ),
        }

        if imagen_tmdb and not video.get("youtube_image"):
            episodio["imagen"] = imagen_tmdb

        episodios_por_serie[serie_id][temporada][numero_ep] = episodio

    # Convertir el catálogo interno al formato que espera series.html.
    lista_series = list(series_por_nombre.values())
    salida_episodios = {}

    for serie in lista_series:
        serie_id = serie["id"]
        temporadas = episodios_por_serie.get(serie_id, {})
        lista_temporadas = []

        for numero_temporada in sorted(temporadas):
            capitulos = [
                temporadas[numero_temporada][numero_ep]
                for numero_ep in sorted(temporadas[numero_temporada])
            ]

            lista_temporadas.append({
                "numero": numero_temporada,
                "capitulos": capitulos,
            })

        salida_episodios[serie_id] = lista_temporadas

    return lista_series, salida_episodios


# ============================================================
# EJECUCION PRINCIPAL
# ============================================================

def main():
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "No está configurada la clave YOUTUBE_API_KEY."
        )

    series_anteriores = leer_json(ARCHIVO_SERIES, [])
    episodios_anteriores = leer_json(ARCHIVO_EPISODIOS, {})

    # Acepta solo el formato de catálogo que utiliza este proyecto.
    if not isinstance(series_anteriores, list):
        series_anteriores = []

    if not isinstance(episodios_anteriores, dict):
        episodios_anteriores = {}

    print("Iniciando actualizador de series SharkTV...")
    print(f"Playlist de YouTube: {PLAYLIST_ID}")
    print(f"Clave TMDB configurada: {'sí' if TMDB_API_KEY else 'no'}")
    videos = obtener_videos_playlist()

    if not videos:
        print(
            "No se han encontrado vídeos. "
            "Se mantienen los catálogos existentes para evitar borrarlos."
        )
        return

    series, episodios = construir_catalogo(
        videos,
        series_anteriores,
        episodios_anteriores
    )

    if not series:
        print(
            "No se detectaron títulos con formato de episodio. "
            "No se sobrescriben los catálogos."
        )
        return

    guardar_json(ARCHIVO_SERIES, series)
    guardar_json(ARCHIVO_EPISODIOS, episodios)

    total_episodios = sum(
        len(temporada.get("capitulos", []))
        for temporadas in episodios.values()
        for temporada in temporadas
    )

    print("----------------------------------------")
    print("ACTUALIZACION DE SHARKTV COMPLETADA")
    print(f"Series encontradas: {len(series)}")
    print(f"Episodios procesados: {total_episodios}")
    print(f"Archivo generado: {ARCHIVO_SERIES.name}")
    print(f"Archivo generado: {ARCHIVO_EPISODIOS.name}")
    print("----------------------------------------")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR EN LA ACTUALIZACION: {error}")
        raise
