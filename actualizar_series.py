import os, re, json, time, unicodedata
from pathlib import Path
from urllib.parse import quote
import requests

ROOT = Path(__file__).resolve().parent
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY', '').strip()
TMDB_API_KEY = os.getenv('TMDB_API_KEY', '').strip()
PLAYLIST_ID = os.getenv('YOUTUBE_SERIES_PLAYLIST_ID', 'PEGA_AQUI_EL_ID_DE_LA_LISTA_DE_SERIES').strip()
YT = 'https://www.googleapis.com/youtube/v3'
TMDB = 'https://api.themoviedb.org/3'
IMG = 'https://image.tmdb.org/t/p/'

# Admite: "Mi Serie S01E01 - El comienzo" y "Mi Serie S1E1 - El comienzo".
PATTERN = re.compile(r'^(?P<name>.+?)\s+S(?P<season>\d{1,2})\s*E(?P<episode>\d{1,3})\b(?:\s*[-–—:|]\s*(?P<epname>.*))?$', re.I)

def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()

def get_json(url, params=None):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def youtube_items():
    items, token = [], None
    while True:
        params = {'part': 'snippet,contentDetails', 'playlistId': PLAYLIST_ID, 'maxResults': 50, 'key': YOUTUBE_API_KEY}
        if token: params['pageToken'] = token
        data = get_json(YT + '/playlistItems', params)
        items.extend(data.get('items', []))
        token = data.get('nextPageToken')
        if not token: break
        time.sleep(0.1)
    return items

def tmdb_search(name, cache):
    key = norm(name)
    if key in cache: return cache[key]
    data = get_json(TMDB + '/search/tv', {'api_key': TMDB_API_KEY, 'language': 'es-ES', 'query': name, 'include_adult': 'false'})
    results = data.get('results', [])
    if not results:
        data = get_json(TMDB + '/search/tv', {'api_key': TMDB_API_KEY, 'language': 'en-US', 'query': name, 'include_adult': 'false'})
        results = data.get('results', [])
    chosen = None
    target = norm(name)
    for item in results:
        if norm(item.get('name')) == target or norm(item.get('original_name')) == target:
            chosen = item; break
    if not chosen and results: chosen = results[0]
    cache[key] = chosen
    return chosen

def image_url(path, size='w780'):
    return IMG + size + path if path else ''

def main():
    if not YOUTUBE_API_KEY or not TMDB_API_KEY:
        raise SystemExit('Faltan los secretos YOUTUBE_API_KEY o TMDB_API_KEY en GitHub.')
    if not PLAYLIST_ID or PLAYLIST_ID.startswith('PEGA_AQUI'):
        raise SystemExit('Configura YOUTUBE_SERIES_PLAYLIST_ID con el ID real de la lista de reproducción.')

    old_series = []
    try: old_series = json.loads((ROOT / 'series.json').read_text(encoding='utf-8'))
    except Exception: pass
    cache = {}
    for s in old_series:
        # Solo cachear IDs numéricos de TMDB; los IDs locales no sirven para consultar su API.
        if s.get('title') and s.get('tmdb_id'):
            cache[norm(s['title'])] = {'id': s.get('tmdb_id'), 'name': s.get('title'), 'poster_path': None, 'backdrop_path': None, '_cached': True}

    grouped = {}
    seen_video_ids = set()
    for item in youtube_items():
        snippet = item.get('snippet', {})
        video_id = (item.get('contentDetails') or {}).get('videoId') or (snippet.get('resourceId') or {}).get('videoId')
        if not video_id or video_id in seen_video_ids: continue
        seen_video_ids.add(video_id)
        raw_title = snippet.get('title', '').strip()
        if raw_title in ('Deleted video', 'Private video'): continue
        match = PATTERN.match(raw_title)
        if not match:
            print('Se omite (no incluye SxxExx):', raw_title)
            continue
        name = match.group('name').strip(' -–—:|')
        season_no, episode_no = int(match.group('season')), int(match.group('episode'))
        key = norm(name)
        video = {'video_id': video_id, 'youtube_title': raw_title, 'episode_title': (match.group('epname') or '').strip(), 'season': season_no, 'episode': episode_no, 'url': 'https://www.youtube.com/watch?v=' + video_id, 'youtube_thumbnail': ((snippet.get('thumbnails') or {}).get('high') or (snippet.get('thumbnails') or {}).get('medium') or (snippet.get('thumbnails') or {}).get('default') or {}).get('url', '')}
        grouped.setdefault(key, {'name': name, 'videos': []})['videos'].append(video)

    series_out, episodes_out = [], {}
    for key, group in grouped.items():
        name = group['name']
        found = tmdb_search(name, cache)
        tmdb_id = found.get('id') if found else None
        # If TMDB doesn't find it, keep the series visible using a stable slug.
        series_id = str(tmdb_id) if tmdb_id else 'local-' + re.sub(r'[^a-z0-9]+', '-', norm(name)).strip('-')
        detail = found or {}
        if tmdb_id:
            try:
                detail = get_json(f'{TMDB}/tv/{tmdb_id}', {'api_key': TMDB_API_KEY, 'language': 'es-ES', 'append_to_response': 'images'})
            except Exception as exc:
                print('Aviso: no se pudieron cargar detalles TMDB de', name, exc)
                detail = found or {}
        poster = image_url(detail.get('poster_path'), 'w780')
        backdrop = image_url(detail.get('backdrop_path'), 'original')
        logo = ''
        logos = ((detail.get('images') or {}).get('logos') or [])
        if logos:
            logos.sort(key=lambda x: (0 if x.get('iso_639_1') in ('es', 'en', None) else 1, -(x.get('vote_average') or 0)))
            logo = image_url(logos[0].get('file_path'), 'w780')
        genre_names = [g.get('name', '') for g in (detail.get('genres') or []) if g.get('name')]
        series_out.append({'id': series_id, 'tmdb_id': tmdb_id, 'type': 'series', 'title': detail.get('name') or detail.get('original_name') or name, 'poster': poster or (group['videos'][0].get('youtube_thumbnail') or ''), 'backdrop': backdrop, 'logo': logo, 'overview': detail.get('overview') or '', 'first_air_date': detail.get('first_air_date') or '', 'genres': ', '.join(genre_names), 'vote_average': detail.get('vote_average') or 0, 'youtube_title_example': group['videos'][0]['youtube_title']})

        seasons_map = {}
        for video in group['videos']:
            sn, en = video['season'], video['episode']
            seasons_map.setdefault(sn, {})[en] = {'numero': en, 'titulo': video['episode_title'] or f"Capítulo {en}", 'imagen': video['youtube_thumbnail'], 'url': video['url'], 'youtube_id': video['video_id'], 'youtube_title': video['youtube_title']}
        # TMDB fills episode title, overview and still when that season/episode exists.
        if tmdb_id:
            for sn in sorted(seasons_map):
                try:
                    season_data = get_json(f'{TMDB}/tv/{tmdb_id}/season/{sn}', {'api_key': TMDB_API_KEY, 'language': 'es-ES'})
                    tmdb_eps = {int(e['episode_number']): e for e in season_data.get('episodes', []) if e.get('episode_number') is not None}
                    for en, ep in seasons_map[sn].items():
                        te = tmdb_eps.get(en)
                        if te:
                            ep['titulo'] = te.get('name') or ep['titulo']
                            ep['descripcion'] = te.get('overview') or ''
                            ep['imagen'] = image_url(te.get('still_path'), 'w780') or ep['imagen']
                except Exception as exc:
                    print(f'Aviso: TMDB temporada {sn} de {name}: {exc}')
        seasons = []
        for sn in sorted(seasons_map):
            eps = [seasons_map[sn][en] for en in sorted(seasons_map[sn])]
            seasons.append({'numero': sn, 'capitulos': eps})
        episodes_out[series_id] = seasons

    series_out.sort(key=lambda s: s['title'].casefold())
    (ROOT / 'series.json').write_text(json.dumps(series_out, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'episodios.json').write_text(json.dumps(episodes_out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Actualización terminada: {len(series_out)} series, {sum(len(t["capitulos"]) for seasons in episodes_out.values() for t in seasons)} episodios.')

if __name__ == '__main__':
    main()
