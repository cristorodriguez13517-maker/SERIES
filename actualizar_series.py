import os, re, json, time
from pathlib import Path
import requests

YOUTUBE_KEY=os.getenv('YOUTUBE_API_KEY','').strip()
TMDB_KEY=os.getenv('TMDB_API_KEY','').strip()
PLAYLIST_ID=os.getenv('YOUTUBE_SERIES_PLAYLIST_ID','PLAeNuSpPHAsk').strip()
ROOT=Path(__file__).resolve().parent
YAPI='https://www.googleapis.com/youtube/v3/playlistItems'
TAPI='https://api.themoviedb.org/3'
IMG='https://image.tmdb.org/t/p/'
s=requests.Session()

def get(url, params):
    r=s.get(url,params=params,timeout=30); r.raise_for_status(); return r.json()

def youtube_videos():
    out=[]; token=None; seen=set()
    while True:
        p={'part':'snippet,contentDetails','playlistId':PLAYLIST_ID,'maxResults':50,'key':YOUTUBE_KEY}
        if token: p['pageToken']=token
        data=get(YAPI,p)
        for it in data.get('items',[]):
            sn=it.get('snippet') or {}; vid=(sn.get('resourceId') or {}).get('videoId'); title=(sn.get('title') or '').strip()
            if not vid or vid in seen or title.lower() in {'private video','deleted video','vídeo privado','video privado','vídeo eliminado','video eliminado'}: continue
            seen.add(vid); out.append({'youtube_id':vid,'youtube_title':title})
        token=data.get('nextPageToken')
        if not token: return out

def parse(title):
    m=re.search(r'\bS(\d{1,2})E(\d{1,3})\b',title,re.I)
    if not m: return None
    prefix=title[:m.start()].strip(); ym=re.search(r'\((19\d{2}|20\d{2})\)\s*$',prefix)
    year=ym.group(1) if ym else ''
    name=re.sub(r'\s*\((19\d{2}|20\d{2})\)\s*$','',prefix).strip(' -–—:|')
    return {'name':name,'year':year,'season':int(m.group(1)),'number':int(m.group(2)),'episode_title':title[m.end():].strip(' -–—:|')} if name else None

def search_tv(name,year):
    for lang in ('es-ES','en-US'):
        p={'api_key':TMDB_KEY,'language':lang,'query':name,'page':1}
        if year: p['first_air_date_year']=year
        d=get(TAPI+'/search/tv',p); results=d.get('results') or []
        if not results and year:
            p.pop('first_air_date_year',None); d=get(TAPI+'/search/tv',p); results=d.get('results') or []
        if results:
            return next((x for x in results if (x.get('name') or '').casefold()==name.casefold()),results[0])
    return None

def main():
    if not YOUTUBE_KEY or not TMDB_KEY: raise SystemExit('Faltan los secretos YOUTUBE_API_KEY o TMDB_API_KEY.')
    videos=youtube_videos(); print(f'Vídeos leídos de YouTube: {len(videos)}')
    groups={}
    for v in videos:
        p=parse(v['youtube_title'])
        if not p: print('OMITIDO (falta SxxExx): '+v['youtube_title']); continue
        v.update(p); k=(p['name'].casefold(),p['year']); groups.setdefault(k,{'name':p['name'],'year':p['year'],'videos':[]})['videos'].append(v)
    print(f'Vídeos con formato SxxExx: {sum(len(g["videos"]) for g in groups.values())}')
    series=[]; episodes={}
    for g in groups.values():
        print('Buscando en TMDB: '+g['name']+' '+g['year'])
        found=search_tv(g['name'],g['year'])
        if not found: print('NO ENCONTRADA EN TMDB: '+g['name']); continue
        tid=found['id']
        try: d=get(f'{TAPI}/tv/{tid}',{'api_key':TMDB_KEY,'language':'es-ES'})
        except requests.RequestException as e: print('Error detalles TMDB:',e); d=found
        try: im=get(f'{TAPI}/tv/{tid}/images',{'api_key':TMDB_KEY,'include_image_language':'es,en,null'})
        except requests.RequestException: im={}
        logos=im.get('logos') or []; logo=None
        for lang in ('es','en',None):
            logo=next((x.get('file_path') for x in logos if x.get('iso_639_1')==lang and x.get('file_path')),None)
            if logo: break
        if not logo: logo=next((x.get('file_path') for x in logos if x.get('file_path')),None)
        back=d.get('backdrop_path') or found.get('backdrop_path')
        series.append({'id':tid,'tmdb_id':tid,'title':d.get('name') or found.get('name') or g['name'],'poster':IMG+'w780'+d['poster_path'] if d.get('poster_path') else None,'logo':IMG+'w780'+logo if logo else None,'backdrop':IMG+'original'+back if back else None,'overview':d.get('overview') or found.get('overview') or '', 'first_air_date':d.get('first_air_date') or found.get('first_air_date') or '', 'genres':[x.get('name') for x in d.get('genres',[]) if x.get('name')], 'vote_average':d.get('vote_average') or found.get('vote_average') or 0})
        seasons={}
        for v in g['videos']:
            sn,en=v['season'],v['number']
            if sn not in seasons:
                try: sd=get(f'{TAPI}/tv/{tid}/season/{sn}',{'api_key':TMDB_KEY,'language':'es-ES'})
                except requests.RequestException as e: print('Aviso temporada:',e); sd={}
                seasons[sn]={int(x.get('episode_number',0)):x for x in sd.get('episodes',[])}
            ep=seasons[sn].get(en,{})
            still=ep.get('still_path')
            episodes.setdefault(str(tid),{})
            episodes[str(tid)].setdefault(sn,[])
            episodes[str(tid)][sn].append({'numero':en,'titulo':ep.get('name') or v['episode_title'] or f'Capítulo {en}','url':'https://www.youtube.com/watch?v='+v['youtube_id'],'youtube_id':v['youtube_id'],'imagen':IMG+'w780'+still if still else f'https://img.youtube.com/vi/{v["youtube_id"]}/hqdefault.jpg'})
        time.sleep(.1)
    # Structure required by series.html: {TMDB_ID: [{numero, capitulos:[...]}]}
    ep_final={sid:[{'numero':sn,'capitulos':sorted(caps,key=lambda x:x['numero'])} for sn,caps in sorted(seasons.items())] for sid,seasons in episodes.items()}
    (ROOT/'series.json').write_text(json.dumps(series,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'episodios.json').write_text(json.dumps(ep_final,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Series encontradas en TMDB: {len(series)}')
    print(f'Series con episodios: {len(ep_final)}')
    print('JSON generados: series.json y episodios.json')

if __name__ == '__main__': main()
