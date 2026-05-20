"""
Beatport Labels Monitor Agent
Топ-лейблы по жанрам с приоритетами → Telegram дайджест.
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

TOP_LABELS_PER_CATEGORY = 5   # топ-5 лейблов на категорию
TOP_RELEASES_PER_LABEL = 2    # последних релизов на лейбл
TRACKS_PER_GENRE = 100        # треков для анализа на жанр


# ── Авторизация ───────────────────────────────────────────────

def get_access_token():
    s = requests.Session()
    s.post(f'{BASE}/auth/login/', json={'username': USERNAME, 'password': PASSWORD}).raise_for_status()
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


def get_top_labels_for_genre(token, genre_id, top_n=5):
    """Топ лейблов в жанре по количеству треков в чарте."""
    label_counter = Counter()
    label_names = {}
    try:
        tracks = api_get(token, '/catalog/tracks/',
                         genre_id=genre_id,
                         ordering='-publish_date',
                         per_page=TRACKS_PER_GENRE)
        for track in (tracks if isinstance(tracks, list) else []):
            label = track.get('release', {}).get('label') or track.get('label') or {}
            if label.get('id'):
                lid = label['id']
                label_counter[lid] += 1
                label_names[lid] = label.get('name', 'Unknown')
    except Exception as e:
        print(f'  Ошибка жанра {genre_id}: {e}')
    return [(label_names[lid], lid, cnt) for lid, cnt in label_counter.most_common(top_n)]


def get_latest_releases(token, label_id, count=2):
    """Последние релизы лейбла."""
    try:
        releases = api_get(token, '/catalog/releases/',
                           label_id=label_id,
                           ordering='-publish_date',
                           per_page=count)
        result = []
        for rel in (releases if isinstance(releases, list) else [])[:count]:
            artists = ', '.join(a.get('name', '') for a in rel.get('artists', []))
            slug = rel.get('slug', '')
            rid = rel.get('id', '')
            result.append({
                'name': rel.get('name', '—'),
                'artists': artists,
                'date': rel.get('publish_date', '—')[:10],
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


def send_in_parts(parts):
    """Отправляем каждую категорию отдельным сообщением."""
    for part in parts:
        send_telegram(part)


# ── Главная логика ─────────────────────────────────────────────

def run():
    print('🔐 Авторизация...')
    token = get_access_token()
    print('✅ Токен получен')

    now = datetime.now().strftime('%d.%m.%Y')
    messages = []

    # Заголовок
    messages.append(f'🎵 <b>Beatport Labels Monitor</b>\n📅 {now}\nТоп лейблов по жанрам:\n')

    for cat in GENRE_CATEGORIES:
        print(f"\n{cat['emoji']} Обрабатываю {cat['label']}...")

        # Суммируем лейблы по всем жанрам категории
        combined_counter = Counter()
        label_names = {}

        for genre in cat['genres']:
            print(f"  → {genre['name']}")
            top = get_top_labels_for_genre(token, genre['id'], top_n=20)
            for name, lid, cnt in top:
                combined_counter[lid] += cnt
                label_names[lid] = name

        top_labels = combined_counter.most_common(TOP_LABELS_PER_CATEGORY)
        genre_names = ' · '.join(g['name'] for g in cat['genres'])

        lines = [f"{cat['emoji']} <b>{cat['label']}</b>: {genre_names}\n"]

        for i, (lid, cnt) in enumerate(top_labels, 1):
            name = label_names[lid]
            lines.append(f'<b>{i}. {name}</b> — {cnt} треков')
            releases = get_latest_releases(token, lid, count=TOP_RELEASES_PER_LABEL)
            for rel in releases:
                artists = f' · {rel["artists"]}' if rel['artists'] else ''
                lines.append(f'   └ <a href="{rel["url"]}">{rel["name"]}</a>{artists} ({rel["date"]})')
            lines.append('')

        messages.append('\n'.join(lines))

    print('\n📨 Отправляю в Telegram...')
    send_in_parts(messages)
    print('✅ Готово!')


if __name__ == '__main__':
    run()
