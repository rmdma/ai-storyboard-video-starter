"""
Beatport Labels Monitor
Логика: агрегируем 30+ DJ-чартов жанра → самые часто встречающиеся лейблы
        → их свежие релизы → Telegram
"""

import os
import re
import requests
from collections import Counter
from datetime import datetime

# ── Конфигурация ──────────────────────────────────────────────
BASE = 'https://api.beatport.com/v4'
CLIENT_ID = '0GIvkCltVIuPkkwSJHp6NDb3s0potTjLBQr388Dd'
REDIRECT_URI = f'{BASE}/auth/o/post-message/'
USERNAME  = os.environ.get('BEATPORT_USERNAME', 'alexrammfeld')
PASSWORD  = os.environ.get('BEATPORT_PASSWORD', 'voWgah-0bismo-cacmyb')
TG_TOKEN  = os.environ.get('TELEGRAM_TOKEN',   '8276581936:AAE2hU16sN2tdUQ40cNDC73iQQxXIazN4vY')
TG_CHAT   = os.environ.get('TELEGRAM_CHAT_ID', '438087649')

TOP_LABELS   = 5    # лейблов на категорию
TOP_RELEASES = 2    # релизов на лейбл
CHARTS_LIMIT = 30   # сколько DJ-чартов анализируем на жанр

# ── Жанры по приоритетам ──────────────────────────────────────
CATEGORIES = [
    {'emoji': '🔴', 'name': 'Приоритет 1', 'genres': [
        {'id': 90, 'name': 'Melodic House & Techno'},
        {'id': 15, 'name': 'Progressive House'},
        {'id': 11, 'name': 'Tech House'},
    ]},
    {'emoji': '🟠', 'name': 'Приоритет 2', 'genres': [
        {'id': 7,  'name': 'Trance (Main Floor)'},
        {'id': 6,  'name': 'Techno (Peak Time)'},
        {'id': 12, 'name': 'Deep House'},
    ]},
    {'emoji': '🟡', 'name': 'Приоритет 3', 'genres': [
        {'id': 89, 'name': 'Afro House'},
        {'id': 39, 'name': 'Dance / Pop'},
        {'id': 5,  'name': 'House'},
    ]},
    {'emoji': '🟢', 'name': 'Приоритет 4', 'genres': [
        {'id': 1,  'name': 'Drum & Bass'},
        {'id': 9,  'name': 'Breaks / Breakbeat'},
        {'id': 86, 'name': 'UK Garage / Bassline'},
        {'id': 95, 'name': '140 / Deep Dubstep / Grime'},
        {'id': 18, 'name': 'Dubstep'},
        {'id': 38, 'name': 'Trap / Future Bass'},
    ]},
]

# ── Чёрный список дистрибьюторов ──────────────────────────────
DISTRIBUTORS = [
    'distrokid', 'tunecore', 'cd baby', 'cdbaby', 'amuse', 'spinnup',
    'awal', 'the orchard', 'empire distribution', 'unitedmasters',
    'routenote', 'ditto', 'stem', 'believe', 'ingrooves', 'fuga',
    'kobalt', 'symphonic', 'horus music', 'imusician', 'emubands',
    'landr', 'rebeat', 'zebralution', 'lw recordings', 'recordjet',
    'goost music', 'create music group', 'noumena', 'sounds array',
    'right hand music group', 'fresh tunes', 'repost network',
]

def is_distributor(name):
    n = name.lower()
    return any(d in n for d in DISTRIBUTORS)


# ── OAuth ─────────────────────────────────────────────────────

def get_token():
    s = requests.Session()
    s.post(f'{BASE}/auth/login/',
           json={'username': USERNAME, 'password': PASSWORD}).raise_for_status()
    r = s.get(f'{BASE}/auth/o/authorize/', params={
        'response_type': 'code', 'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI
    }, allow_redirects=False)
    code = re.search(r'code=([^&]+)', r.headers.get('Location', ''))
    if not code:
        raise Exception('Auth code не найден')
    r2 = s.post(f'{BASE}/auth/o/token/', params={
        'code': code.group(1), 'grant_type': 'authorization_code',
        'redirect_uri': REDIRECT_URI, 'client_id': CLIENT_ID
    })
    r2.raise_for_status()
    return r2.json()['access_token']


# ── API ───────────────────────────────────────────────────────

def get(token, endpoint, **params):
    r = requests.get(f'{BASE}{endpoint}',
                     headers={'Authorization': f'Bearer {token}'},
                     params=params, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d.get('results', d) if isinstance(d, dict) and 'results' in d else d


# ── Основная логика ───────────────────────────────────────────

def genre_top_labels(token, genre_id, n_charts=CHARTS_LIMIT):
    """
    Агрегируем n_charts DJ-чартов жанра.
    Считаем сколько разных чартов включают каждый лейбл.
    Чем больше DJ добавили трек → тем популярнее лейбл.
    """
    # Получаем свежие чарты жанра
    charts = get(token, '/catalog/charts/', genre_id=genre_id, per_page=n_charts)
    if not isinstance(charts, list) or not charts:
        return Counter(), {}

    label_chart_count = Counter()   # сколько чартов упоминают лейбл
    label_track_count = Counter()   # сколько треков лейбла в чартах суммарно
    label_names = {}

    for chart in charts:
        chart_id = chart.get('id')
        if not chart_id:
            continue
        try:
            tracks = get(token, f'/catalog/charts/{chart_id}/tracks/', per_page=100)
            seen_labels_this_chart = set()
            for t in (tracks if isinstance(tracks, list) else []):
                label = t.get('release', {}).get('label') or t.get('label') or {}
                lname = label.get('name', '')
                if not label.get('id') or is_distributor(lname):
                    continue
                lid = label['id']
                label_names[lid] = lname
                label_track_count[lid] += 1
                if lid not in seen_labels_this_chart:
                    label_chart_count[lid] += 1
                    seen_labels_this_chart.add(lid)
        except Exception:
            continue

    return label_chart_count, label_track_count, label_names


def latest_releases(token, label_id, n=TOP_RELEASES):
    """Свежие релизы лейбла — вышедшие и пре-релизы раздельно."""
    today = datetime.now().date()
    # Берём с запасом чтобы набрать n вышедших + n пре-релизов
    rels = get(token, '/catalog/releases/',
               label_id=label_id, ordering='-publish_date', per_page=n * 6)
    released = []
    upcoming = []
    for r in (rels if isinstance(rels, list) else []):
        artists = ', '.join(a.get('name', '') for a in r.get('artists', []))
        date_str = (r.get('publish_date') or r.get('new_release_date') or '')[:10]
        url = f"https://www.beatport.com/release/{r.get('slug','')}/{r.get('id','')}"
        item = {'name': r.get('name', '—'), 'artists': artists,
                'date': date_str, 'url': url}
        try:
            rel_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            if rel_date > today:
                upcoming.append(item)
            else:
                released.append(item)
        except Exception:
            released.append(item)
    return released[:n], upcoming[:n]


# ── Telegram ──────────────────────────────────────────────────

def tg(text):
    requests.post(
        f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
        json={'chat_id': TG_CHAT, 'text': text,
              'parse_mode': 'HTML', 'disable_web_page_preview': True},
        timeout=10
    )


# ── Запуск ────────────────────────────────────────────────────

def run():
    print('🔐 Авторизация...')
    token = get_token()
    print('✅ OK\n')

    now = datetime.now().strftime('%d.%m.%Y')
    tg(f'🎵 <b>Beatport Labels Monitor</b> · {now}\n'
       f'Топ лейблов по {CHARTS_LIMIT} DJ-чартам на жанр')

    for cat in CATEGORIES:
        print(f"{cat['emoji']} {cat['name']}")
        total_charts = Counter()
        total_tracks = Counter()
        all_names = {}

        for genre in cat['genres']:
            print(f"   {genre['name']}", end=' ... ')
            chart_c, track_c, names = genre_top_labels(token, genre['id'])
            total_charts.update(chart_c)
            total_tracks.update(track_c)
            all_names.update(names)
            print(f"{sum(chart_c.values())} упоминаний в {len(chart_c)} лейблах")

        # Сортируем: сначала по числу чартов, потом по числу треков
        top = sorted(
            total_charts.keys(),
            key=lambda lid: (total_charts[lid], total_tracks[lid]),
            reverse=True
        )[:TOP_LABELS]

        genre_list = ' · '.join(g['name'] for g in cat['genres'])
        lines = [f"{cat['emoji']} <b>{cat['name']}</b>\n<i>{genre_list}</i>\n"]

        for i, lid in enumerate(top, 1):
            name = all_names[lid]
            n_charts = total_charts[lid]
            n_tracks = total_tracks[lid]
            lines.append(f'<b>{i}. {name}</b> — в {n_charts} чартах ({n_tracks} треков)')
            released, upcoming = latest_releases(token, lid)
            for rel in released:
                a = f' · {rel["artists"]}' if rel['artists'] else ''
                lines.append(f'   ✅ <a href="{rel["url"]}">{rel["name"]}</a>{a} ({rel["date"]})')
            for rel in upcoming:
                a = f' · {rel["artists"]}' if rel['artists'] else ''
                lines.append(f'   ⏳ <a href="{rel["url"]}">{rel["name"]}</a>{a} ({rel["date"]})')
            lines.append('')

        tg('\n'.join(lines))

    tg('✅ Готово')
    print('\n✅ Дайджест отправлен!')


if __name__ == '__main__':
    run()
