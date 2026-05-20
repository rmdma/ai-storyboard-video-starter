"""
Beatport Labels Monitor Agent
Собирает топ-лейблы с Beatport и отправляет дайджест в Telegram.
"""

import os
import requests
import re
import json
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

# ── Авторизация ───────────────────────────────────────────────

def get_access_token():
    """Получаем OAuth токен через username/password."""
    s = requests.Session()

    # 1. Логин
    r = s.post(f'{BASE}/auth/login/', json={'username': USERNAME, 'password': PASSWORD})
    r.raise_for_status()

    # 2. Получаем auth code
    r2 = s.get(f'{BASE}/auth/o/authorize/', params={
        'response_type': 'code',
        'client_id': CLIENT_ID,
        'redirect_uri': REDIRECT_URI
    }, allow_redirects=False)

    location = r2.headers.get('Location', '')
    code_match = re.search(r'code=([^&]+)', location)
    if not code_match:
        raise Exception(f'Auth code не найден. Location: {location}')
    auth_code = code_match.group(1)

    # 3. Обмениваем code на токен
    r3 = s.post(f'{BASE}/auth/o/token/', params={
        'code': auth_code,
        'grant_type': 'authorization_code',
        'redirect_uri': REDIRECT_URI,
        'client_id': CLIENT_ID
    })
    r3.raise_for_status()
    return r3.json()['access_token']


# ── API запросы ───────────────────────────────────────────────

def api_get(token, endpoint, **params):
    """GET запрос к Beatport API."""
    headers = {'Authorization': f'Bearer {token}'}
    r = requests.get(f'{BASE}{endpoint}', headers=headers, params=params)
    r.raise_for_status()
    return r.json()


def get_top_labels_from_charts(token, top_n=10):
    """
    Анализируем топ-100 треков всех жанров и считаем,
    сколько раз каждый лейбл встречается в чартах.
    """
    label_counter = Counter()
    label_names = {}

    # Получаем топ-100 треков (global)
    data = api_get(token, '/catalog/tracks/',
                   ordering='-publish_date',
                   per_page=100,
                   is_charted=True)

    tracks = data.get('results', data) if isinstance(data, dict) else data

    for track in tracks:
        label = track.get('release', {}).get('label', {})
        if not label:
            label = track.get('label', {})
        if label and label.get('id'):
            lid = label['id']
            lname = label.get('name', 'Unknown')
            label_counter[lid] += 1
            label_names[lid] = lname

    # Возвращаем топ N лейблов
    top = label_counter.most_common(top_n)
    return [(label_names.get(lid, 'Unknown'), lid, count) for lid, count in top]


def get_top100_chart_labels(token, top_n=10):
    """Берём треки из официальных чартов и считаем лейблы."""
    label_counter = Counter()
    label_names = {}

    # Получаем список чартов
    charts_data = api_get(token, '/catalog/charts/', per_page=20)
    charts = charts_data.get('results', charts_data) if isinstance(charts_data, dict) else charts_data

    for chart in charts[:5]:  # берём первые 5 чартов
        chart_id = chart.get('id')
        if not chart_id:
            continue
        try:
            chart_tracks = api_get(token, f'/catalog/charts/{chart_id}/tracks/', per_page=100)
            tracks = chart_tracks.get('results', chart_tracks) if isinstance(chart_tracks, dict) else chart_tracks
            for track in tracks:
                label = track.get('release', {}).get('label', {}) or track.get('label', {})
                if label and label.get('id'):
                    lid = label['id']
                    label_counter[lid] += 1
                    label_names[lid] = label.get('name', 'Unknown')
        except Exception:
            continue

    top = label_counter.most_common(top_n)
    return [(label_names.get(lid, 'Unknown'), lid, count) for lid, count in top]


def get_latest_releases_for_label(token, label_id, label_name, count=3):
    """Получаем последние релизы конкретного лейбла."""
    try:
        data = api_get(token, '/catalog/releases/',
                       label_id=label_id,
                       ordering='-publish_date',
                       per_page=count)
        releases = data.get('results', data) if isinstance(data, dict) else data
        result = []
        for rel in releases[:count]:
            result.append({
                'name': rel.get('name', '—'),
                'artists': ', '.join(a.get('name', '') for a in rel.get('artists', [])),
                'date': rel.get('publish_date', '—'),
                'url': f"https://www.beatport.com/release/{rel.get('slug', '')}/{rel.get('id', '')}"
            })
        return result
    except Exception:
        return []


# ── Telegram ──────────────────────────────────────────────────

def send_telegram(message):
    """Отправляем сообщение в Telegram."""
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
    print('✅ Токен получен')

    print('📊 Анализирую чарты...')

    # Пробуем сначала через чарты, затем через треки
    top_labels = get_top100_chart_labels(token, top_n=10)

    if not top_labels:
        print('Чарты пусты, пробую через треки...')
        top_labels = get_top_labels_from_charts(token, top_n=10)

    print(f'Найдено лейблов: {len(top_labels)}')

    # Формируем сообщение
    now = datetime.now().strftime('%d.%m.%Y %H:%M')
    lines = [f'🎵 <b>Beatport: Топ лейблы</b>\n📅 {now}\n']

    for i, (name, label_id, count) in enumerate(top_labels, 1):
        lines.append(f'<b>{i}. {name}</b> — {count} треков в чартах')

        # Последние релизы
        releases = get_latest_releases_for_label(token, label_id, name, count=2)
        for rel in releases:
            artists = f' · {rel["artists"]}' if rel['artists'] else ''
            lines.append(f'   └ <a href="{rel["url"]}">{rel["name"]}</a>{artists} ({rel["date"]})')

        lines.append('')

    message = '\n'.join(lines)

    print('📨 Отправляю в Telegram...')
    if send_telegram(message):
        print('✅ Дайджест отправлен!')
    else:
        print('❌ Ошибка отправки в Telegram')
        print(message)


if __name__ == '__main__':
    run()
