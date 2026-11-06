#!/usr/bin/env python3
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.message import EmailMessage
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys
import tempfile
import time

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
URL = 'https://www.altstu.ru/main/schedule/teacher/1059/'
STATE = Path.home() / '.local/state/schedule-monitor'
OLD_STATE = Path.home() / '.local/state/altstu-monitor/current.json'
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
log = logging.getLogger('schedule-monitor')


def load_env():
    path = ROOT / '.env'
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip().strip('"\'')
        os.environ.setdefault(key.strip(), value)


def clean(value):
    return ' '.join(value.split())


def parse_schedule(html):
    soup = BeautifulSoup(html, 'html.parser')
    block = soup.select_one('#inner_content .schedule') or soup.select_one('.schedule')
    if block is None:
        raise ValueError('Блок расписания не найден')
    title = block.find_previous('h2')
    teacher = clean(title.get_text(' ', strip=True)) if title else ''
    lessons = []
    tables = block.select('table')
    if not tables:
        raise ValueError('Таблицы расписания не найдены')
    for table in tables:
        week = table.find_previous('h3')
        if not week or not re.search(r'\d{2}\.\d{2}\.\d{4}', week.get_text(' ', strip=True)):
            raise ValueError('Не найдена дата недели')
        date = None
        for row in table.select('tbody tr'):
            day = row.select_one('th.day')
            if day:
                match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', day.get_text(' ', strip=True))
                if not match:
                    raise ValueError('Непонятный формат даты')
                date = f'{match[3]}-{match[2]}-{match[1]}'
                continue
            cells = row.find_all('td', recursive=False)
            if not cells:
                continue
            if len(cells) != 4 or not date:
                raise ValueError('Непонятная строка расписания')
            time, group, subject, room = [clean(cell.get_text(' ', strip=True)) for cell in cells]
            if not re.fullmatch(r'\d{2}:\d{2}\s*[-–—]\s*\d{2}:\d{2}', time):
                raise ValueError('Непонятный формат времени')
            flags = sorted({cls for item in [row, *cells] for cls in item.get('class', []) if cls in ('once', 'once-exam')})
            lessons.append({'date': date, 'time': re.sub(r'\s*[-–—]\s*', '-', time), 'group': group, 'subject': subject, 'room': room, 'flags': flags})
    if not lessons:
        raise ValueError('Расписание пустое')
    lessons.sort(key=lambda x: (x['date'], x['time'], x['group'], x['subject'], x['room'], x['flags']))
    return {'teacher': teacher, 'lessons': lessons}


def encode(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(data):
    return hashlib.sha256(encode(data)).hexdigest()


def describe(item):
    return f"{item['date']} {item['time']} | {item['group']} | {item['subject']} | ауд. {item['room'] or 'не указана'}"


def diff(old, new):
    key = lambda x: (x['date'], x['time'], x['group'])
    left, right = defaultdict(list), defaultdict(list)
    for item in old['lessons']:
        left[key(item)].append(item)
    for item in new['lessons']:
        right[key(item)].append(item)
    result = []
    for k in sorted(left.keys() | right.keys()):
        a, b = Counter(encode(x) for x in left[k]), Counter(encode(x) for x in right[k])
        common = a & b
        removed = [json.loads(x) for x, count in sorted((a - common).items()) for _ in range(count)]
        added = [json.loads(x) for x, count in sorted((b - common).items()) for _ in range(count)]
        for before, after in zip(removed, added):
            fields = [f'{field}: {before[field] or "—"} → {after[field] or "—"}' for field in ('subject', 'room', 'flags') if before[field] != after[field]]
            result.append(f'ИЗМЕНЕНО: {k[0]} {k[1]} | {k[2]}\n  ' + '; '.join(fields))
        result += ['УДАЛЕНО: ' + describe(x) for x in removed[len(added):]]
        result += ['ДОБАВЛЕНО: ' + describe(x) for x in added[len(removed):]]
    if old.get('teacher') != new.get('teacher'):
        result.insert(0, f"ПРЕПОДАВАТЕЛЬ: {old.get('teacher')} → {new.get('teacher')}")
    return result


def save(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def send_email(changes, teacher, url):
    names = ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM', 'SMTP_TO')
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise ValueError('Не заполнено в .env: ' + ', '.join(missing))
    msg = EmailMessage()
    msg['Subject'] = f'Изменилось расписание: {teacher}'
    msg['From'] = os.environ['SMTP_FROM']
    msg['To'] = os.environ['SMTP_TO']
    msg.set_content('\n\n'.join(changes) + f'\n\nРасписание: {url}')
    port = int(os.getenv('SMTP_PORT', '587'))
    security = os.getenv('SMTP_SECURITY', 'ssl' if port == 465 else 'starttls').lower()
    if security == 'ssl':
        connection = smtplib.SMTP_SSL(os.environ['SMTP_HOST'], port, timeout=30, context=ssl.create_default_context())
    elif security == 'starttls':
        connection = smtplib.SMTP(os.environ['SMTP_HOST'], port, timeout=30)
    else:
        raise ValueError('SMTP_SECURITY должен быть ssl или starttls')
    with connection as smtp:
        if security == 'starttls':
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
        smtp.send_message(msg)



def cleanup_snapshots(folder):
    if not folder.exists():
        return
    now = time.time()
    keep = int(os.getenv('SNAPSHOT_MAX_COUNT', '100'))
    limits = {
        '.html': int(os.getenv('SNAPSHOT_RETENTION_HTML', '30')),
        '.json': int(os.getenv('SNAPSHOT_RETENTION_JSON', '90'))
    }
    if keep < 0 or any(days < 0 for days in limits.values()):
        raise ValueError('Настройки очистки не могут быть отрицательными')
    for ext, days in limits.items():
        files = [p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and p.suffix == ext]
        files.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
        deleted = 0
        for index, path in enumerate(files):
            if (days and now - path.stat().st_mtime > days * 86400) or (keep and index >= keep):
                path.unlink()
                deleted += 1
        if deleted:
            log.info('Удалено старых %s-снапшотов: %s', ext, deleted)


def cleanup_safe(folder):
    try:
        cleanup_snapshots(folder)
    except (OSError, ValueError) as error:
        log.warning('Не удалось очистить снапшоты: %s', error)


def main():
    load_env()
    parser = argparse.ArgumentParser(description='Schedule Monitor')
    parser.add_argument('--state-dir', default=os.getenv('STATE_DIR', str(STATE)))
    parser.add_argument('--url', default=os.getenv('SCHEDULE_URL', URL))
    parser.add_argument('--html-file')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--test-email', action='store_true')
    args = parser.parse_args()
    state = Path(args.state_dir).expanduser()
    state.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[
        RotatingFileHandler(state / 'monitor.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ])
    try:
        with (state / '.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.test_email:
                send_email(['Это тестовое письмо от Schedule Monitor.'], 'Проверка почты', args.url)
                log.info('Тестовое письмо отправлено')
                return 0
            if args.html_file:
                html = Path(args.html_file).read_bytes()
            else:
                response = requests.get(args.url, headers=HEADERS, timeout=(10, 35))
                response.raise_for_status()
                if 'html' not in response.headers.get('Content-Type', 'text/html').lower():
                    raise ValueError('Сайт вернул не HTML')
                html = response.content
            data = parse_schedule(html)
            current_hash = digest(data)
            log.info('Загружено %s занятий, SHA256=%s', len(data['lessons']), current_hash)
            snapshots = state / 'snapshots'
            current = state / 'current.json'
            previous = current if current.exists() else OLD_STATE if OLD_STATE.exists() else None
            if previous:
                old = json.loads(previous.read_text(encoding='utf-8'))
                if digest(old) == current_hash:
                    log.info('Изменений нет; письмо не отправлено')
                    if not args.dry_run:
                        if previous != current:
                            save(current, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
                        cleanup_safe(snapshots)
                    return 0
                changes = diff(old, data)
                if not changes:
                    raise ValueError('Хэш изменился, но различия занятий не найдены')
                for change in changes:
                    log.info('%s', change)
                if args.dry_run:
                    return 0
            else:
                changes = []
                if args.dry_run:
                    log.info('Пробный первый запуск; состояние не сохранено')
                    return 0
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            save(snapshots / f'{stamp}-{current_hash[:12]}.html', html)
            save(snapshots / f'{stamp}-{current_hash[:12]}.json', json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
            if changes:
                send_email(changes, data['teacher'], args.url)
                log.info('Письмо отправлено')
            save(current, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
            log.info('Состояние сохранено' if changes else 'Первый запуск: создана база; письмо не отправлено')
            cleanup_safe(snapshots)
            return 0
    except BlockingIOError:
        log.info('Проверка уже выполняется')
        return 0
    except Exception:
        log.exception('Ошибка мониторинга')
        return 1


if __name__ == '__main__':
    sys.exit(main())
