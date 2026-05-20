"""
Beatport Labels Monitor Agent
Топ-лейблы из официальных жанровых чартов → Telegram дайджест.
"""

import os
import requests
import re
from collections import Counter
from datetime import datetime

# ── Конфигурация ──────────────────────────────────────────────
BASE = 'https://api.beatport.com/v4'
CLIENT_ID = '0GIvkCltVIuPkkwSJHp6NDb3s0potTjLBQr388Dd'
REDIRECT_URI = f'{BASE}/auth/o/post-message/'
USERNAME = os.environ.get('BEATPORT_USERNAME', 'alexrammfeld')
PASSWORD = os.environ.get('BEATPORT_PASSWORD', 'voWgah-0bismo-cacmyb')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8276581936:AAE2hU16sN2tdUQ40cNDC73iQQxXIazN4vY')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '438087649')

# ── Жанры по приоритетам ──────────────────────────────────────
GENRE_CATEGORIES = [
    {
        'priority': 1,
        'emoji': '🔴',
        'label': 'Приоритет 1',
        'genres': [
            {'id': 90, 'name': 'Melodic House & Techno'},
            {'id': 15, 'name': 'Progressive House'},
            {'id': 11, 'name': 'Tech House'},
        ]
    },
    {
        'priority': 2,
        'emoji': '🟠',
        'label': 'Приоритет 2',
        'genres': [
            {'id': 7,  'name': 'Trance (Main Floor)'},
            {'id': 6,  'name': 'Techno (Peak Time)'},
            {'id': 12, 'name': 'Deep House'},
        ]
    },
    {
        'priority': 3,
        'emoji': '🟡',
        'label': 'Приоритет 3',
        'genres': [
            {'id': 89, 'name': 'Afro House'},
            {'id': 39, 'name': 'Dance / Pop'},
            {'id': 5,  'name': 'House'},
        ]
    },
    {
        'priority': 4,
        'emoji': '🟢',
        'label': 'Приоритет 4',
        'genres': [
            {'id': 1,  'name': 'Drum & Bass'},
            {'id': 9,  'name': 'Breaks / Breakbeat'},
            {'id': 86, 'name': 'UK Garage / Bassline'},
            {'id': 95, 'name': '140 / Deep Dubstep / Grime'},
            {'id': 18, 'name': 'Dubstep'},
            {'id': 38, 'name': 'Trap / Future Bass'},
        ]
    },
]

TOP_LABELS_PER_CATEGORY = 5
TOP_RELEASES_PER_LABEL = 2

# ── Чёрный список дистрибьюторов ──────────────────────────────
DISTRIBUTOR_BLACKLIST = {
    'distrokid', 'tunecore', 'cd baby', 'cdbaby', 'amuse', 'spinnup',
    'awal', 'the orchard', 'empire distribution', 'unitedmasters',
    'routenote', 'ditto music', 'ditto', 'stem', 'fresh tunes',
    'believe', 'ingrooves', 'songtradr', 'repost network',
    'right hand music group', 'sounds array', 'lw recordings',
    'recordjet', 'goost music', 'create music group', 'iм electronica',
    'noumena', 'fuga', 'kobalt', 'downtown music', 'symphonic',
    'horus music', 'label worx', 'imusician', 'emubands',
    'music gateway', 'soundrop', 'landr', 'delivermytune',
    'onesubmit', 'reverbnation', 'bandcamp', 'octiive', 'cds',
    'rebeat', 'zebralution', 'state 51', 'black hole recordings distributie',
    'sony music entertainment', 'universal music', 'warner music',
}

def is_distributor(label_name: str) -> bool:
    """Проверяем, является ли лейбл дистрибьютором."""
    name_lower = label_name.lower().strip()
    # Точное совпадение
    if name_lower in DISTRIBUTOR_BLACKLIST:
        return True
    # Частичное совпадение для известных агрегаторов
    for d in ['distrokid', 'tunecore', 'cd baby', 'believe', 'amuse',
              'spinnup', 'routenote', 'ditto', 'ingrooves', 'orchard']:
        if d in name_lower:
            return True
    return False


# ── Авторизация ───────────────────────────────────────────────

def get_access_token():
    s = requests.Session()
    s.post(f'{BASE}/auth/login/',
           json={'username': USERNAME, 'password': PASSWORD}).raise_for_status()
    r2 = s.get(f'{BASE}/auth/o/authorize/', params={
        'response_type': 'code', 'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI
    }, allow_redirects=False)
    code = re.search(r'code=([^&]+)', r2.headers.get('Location', ''))
    if not code:
        raise Exception('Не удалось получить auth code')
    r3 = s.post(f'{BASE}/auth/o/token/', params={
        'code': code.group(1), 'grant_type': 'authorization_code',
        'redirect_uri': REDIRECT_URI, 'client_id': CLIENT_ID
    })
    r3.raise_for_status()
    return r3.json()['access_token']


# ── API ───────────────────────────────────────────────────────

def api_get(token, endpoint, **params):
    r = requests.get(f'{BASE}{endpoint}',
                     headers={'Authorization': f'Bearer {token}'},
                     params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get('results', data) if isinstance(data, dict) and 'results' in data else data


def get_genre_top_chart_id(token, genre_id):
    """Находим официальный Top-100 чарт для жанра."""
    try:
        charts = api_get(token, '/catalog/charts/',
                         genre_id=genre_id, per_page=50)
        if not isinstance(charts, list):
            return None
        # Ищем официальный Top 100 — у него нет владельца (Beatport editorial)
        for chart in charts:
            name = chart.get('name', '').lower()
            owner = chart.get('person', {}) or {}
            owner_name = (owner.get('owner_name') or owner.get('name') or '').lower()
            if ('top 100' in name or 'top100' in name) and 'beatport' in owner_name:
                return chart['id']
        # Если не нашли с "beatport" — берём первый Top 100
        for chart in charts:
            name = chart.get('name', '').lower()
            if 'top 100' in name or 'top100' in name:
                return chart['id']
        # Если вообще нет Top 100 — берём первый чарт
        if charts:
            return charts[0]['id']
    except Exception as e:
        print(f'    Ошибка поиска чарта для жанра {genre_id}: {e}')
    return None


def get_top_labels_from_chart(token, chart_id, genre_id):
    """Получаем треки из чарта и считаем лейблы."""
    label_counter = Counter()
    label_names = {}
    try:
        tracks = api_get(token, f'/catalog/charts/{chart_id}/tracks/', per_page=100)
        if not isinstance(tracks, list):
            return {}
        for track in tracks:
            # Проверяем жанр трека
            track_genres = track.get('genre', {})
            if isinstance(track_genres, dict):
                track_genres = [track_genres]
            genre_ids = [g.get('id') for g in track_genres if isinstance(g, dict)]
            # Если жанр не совпадает — пропускаем (строгая фильтрация)
            if genre_ids and genre_id not in genre_ids:
                continue

            label = track.get('release', {}).get('label') or track.get('label') or {}
            if not label.get('id'):
                continue
            lname = label.get('name', '')
            if is_distributor(lname):
                continue
            lid = label['id']
            label_counter[lid] += 1
            label_names[lid] = lname
    except Exception as e:
        print(f'    Ошибка чарта {chart_id}: {e}')
    return label_counter, label_names


def get_top_labels_for_genre(token, genre_id, top_n=20):
    """Топ лейблов жанра из официального чарта."""
    # Сначала пробуем через чарт
    chart_id = get_genre_top_chart_id(token, genre_id)
    label_counter = Counter()
    label_names = {}

    if chart_id:
        result = get_top_labels_from_chart(token, chart_id, genre_id)
        if result and result[0]:
            label_counter, label_names = result

    # Если чарт пустой — фолбэк через треки жанра с фильтром
    if not label_counter:
        try:
            tracks = api_get(token, '/catalog/tracks/',
                             genre_id=genre_id,
                             ordering='-publish_date',
                             per_page=100)
            for track in (tracks if isinstance(tracks, list) else []):
                label = track.get('release', {}).get('label') or track.get('label') or {}
                lname = label.get('name', '')
                if not label.get('id') or is_distributor(lname):
                    continue
                lid = label['id']
                label_counter[lid] += 1
                label_names[lid] = lname
        except Exception as e:
            print(f'    Фолбэк ошибка жанра {genre_id}: {e}')

    return [(label_names[lid], lid, cnt) for lid, cnt in label_counter.most_common(top_n)]


def get_latest_releases(token, label_id, genre_id, count=2):
    """Последние релизы лейбла в конкретном жанре."""
    try:
        releases = api_get(token, '/catalog/releases/',
                           label_id=label_id,
                           genre_id=genre_id,
                           ordering='-publish_date',
                           per_page=count * 3)
        result = []
        for rel in (releases if isinstance(releases, list) else []):
            if len(result) >= count:
                break
            artists = ', '.join(a.get('name', '') for a in rel.get('artists', []))
            slug = rel.get('slug', '')
            rid = rel.get('id', '')
            result.append({
                'name': rel.get('name', '—'),
                'artists': artists,
                'date': (rel.get('publish_date') or rel.get('new_release_date') or '—')[:10],
                'url': f'https://www.beatport.com/release/{slug}/{rid}'
            })
        return result
    except Exception:
        return []


# ── Telegram ──────────────────────────────────────────────────

def send_telegram(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    r = requests.post(url, json={
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    })
    return r.status_code == 200


# ── Главная логика ─────────────────────────────────────────────

def run():
    print('🔐 Авторизация...')
    token = get_access_token()
    print('✅ Токен получен\n')

    now = datetime.now().strftime('%d.%m.%Y')
    send_telegram(f'🎵 <b>Beatport Labels Monitor</b>\n📅 {now}\nТоп лейблов из официальных чартов:')

    for cat in GENRE_CATEGORIES:
        print(f"{cat['emoji']} {cat['label']}...")
        combined_counter = Counter()
        label_names = {}
        # Запоминаем какому жанру принадлежит лейбл (берём первый)
        label_genre = {}

        for genre in cat['genres']:
            print(f"  → {genre['name']}")
            top = get_top_labels_for_genre(token, genre['id'], top_n=20)
            for name, lid, cnt in top:
                combined_counter[lid] += cnt
                label_names[lid] = name
                if lid not in label_genre:
                    label_genre[lid] = genre['id']

        top_labels = combined_counter.most_common(TOP_LABELS_PER_CATEGORY)
        genre_names = ' · '.join(g['name'] for g in cat['genres'])
        lines = [f"{cat['emoji']} <b>{cat['label']}</b>\n<i>{genre_names}</i>\n"]

        for i, (lid, cnt) in enumerate(top_labels, 1):
            name = label_names[lid]
            lines.append(f'<b>{i}. {name}</b> — {cnt} позиций в чарте')
            releases = get_latest_releases(token, lid, label_genre.get(lid, 0), count=TOP_RELEASES_PER_LABEL)
            for rel in releases:
                artists = f' · {rel["artists"]}' if rel['artists'] else ''
                lines.append(f'   └ <a href="{rel["url"]}">{rel["name"]}</a>{artists} ({rel["date"]})')
            lines.append('')

        send_telegram('\n'.join(lines))

    send_telegram('✅ Дайджест завершён')
    print('\n✅ Готово!')


if __name__ == '__main__':
    run()
