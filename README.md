# Schedule Monitor

Небольшой монитор расписания преподавателя для Linux. Скачивает HTML, извлекает занятия из `div.schedule`, сохраняет JSON и сравнивает его SHA-256 с предыдущей проверкой. Если что-то изменилось, отправляет письмо с добавленными, удалёнными и изменёнными занятиями. Если нет — ничего не отправляет.

## Что нужно

- Linux с Python 3 и `python3-venv`
- Интернет и доступ к странице расписания
- Почта с возможностью отправки по SMTP (для Gmail — пароль приложения)

## Установка

Распакуй архив в любое место, например `/home/имя пользователя/название папки`, и открой терминал в этой папке:

```bash
cd /home/asd/b1mbimbambam
bash install.sh
nano .env
chmod 600 .env
```

Если проект расположен в другом месте, замени путь в команде `cd`. Установщик сам определяет реальный путь для systemd.

В `.env` заполни:

```dotenv
SCHEDULE_URL=https://www.altstu.ru/main/schedule/teacher/1059/
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USER=example@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=example@gmail.com
SMTP_TO=example@gmail.com
SNAPSHOT_RETENTION_HTML=10
SNAPSHOT_RETENTION_JSON=20
SNAPSHOT_MAX_COUNT=50
```

`SMTP_USER`, `SMTP_FROM` — твой Gmail. `SMTP_TO` — почта для получения писем. `SMTP_PASSWORD` — 16-символьный пароль приложения Google (вводить без пробелов), **не обычный пароль аккаунта**. Его можно создать на https://myaccount.google.com/apppasswords при доступности этой функции и включённой двухэтапной аутентификации.

## Проверка

```bash
.venv/bin/python monitor.py --dry-run
.venv/bin/python monitor.py
.venv/bin/python monitor.py --test-email
```

`--dry-run` загружает и сравнивает расписание, но не меняет базу и не отправляет письмо. Обычный запуск создаёт базу при первой проверке, без уведомления. `--test-email` отправляет отдельное тестовое письмо и никак не изменяет расписание.

Для проверки парсера без интернета:

```bash
.venv/bin/python monitor.py --html-file tests/sample.html --dry-run
```

## Автозапуск

```bash
systemctl --user enable --now schedule-monitor.timer
systemctl --user status schedule-monitor.timer
systemctl --user list-timers --all
journalctl --user -u schedule-monitor.service -n 50 --no-pager
```

Интервал по умолчанию — один час. Чтобы изменить его, открой:

```bash
nano ~/.config/systemd/user/schedule-monitor.timer
```

Измени `OnUnitActiveSec=1h` на `30min`, `2h` или другое время. Затем:

```bash
systemctl --user daemon-reload
systemctl --user restart schedule-monitor.timer
```

Вручную запустить проверку через systemd:

```bash
systemctl --user start schedule-monitor.service
```

Таймер работает только когда Linux запущен. Для продолжения работы после выхода из учётной записи (но не выключения ПК) можно включить `sudo loginctl enable-linger "$USER"`.

## Файлы

- `monitor.py` — загрузка, сравнение и отправка сообщений.
- `install.sh` — зависимости и установка пользовательских systemd-файлов.
- `systemd/schedule-monitor.timer` — интервал проверок.
- `.env.example` — шаблон настроек.
- `tests/sample.html` — HTML-пример для проверки парсинга.

База и логи находятся в `~/.local/state/schedule-monitor/`:

```text
current.json
monitor.log
snapshots/
```

Ошибки загрузки и отправки письма записываются в лог; `current.json` при них не заменяется. При каждой обнаруженной смене данных создаётся HTML- и JSON-снимок. При регулярной смене опубликованных недель эти недели также будут определяться как изменения.

## Автоочистка снимков

HTML- и JSON-снимки сохраняются в `~/.local/state/schedule-monitor/snapshots/`. После каждой успешной проверки (в том числе без изменений) скрипт удаляет старые снимки. Для удаления достаточно следующей штатной проверки, отдельный таймер не нужен.

Параметры в `.env`:

```dotenv
SNAPSHOT_RETENTION_HTML=10
SNAPSHOT_RETENTION_JSON=20
SNAPSHOT_MAX_COUNT=50
```

- HTML хранится до 10 дней, JSON — до 20 дней.
- Для каждого формата отдельно сохраняется не больше 100 последних файлов (если они моложе установленного срока).
- `0` для срока хранения отключает очистку по возрасту, `0` для количества — ограничение по количеству. Отрицательные значения не допускаются.
- `current.json`, `monitor.log` и другие файлы вне `snapshots/` не удаляются.
- `--dry-run` и `--test-email` не запускают очистку.
- При ошибке запроса или почты очистка не выполняется; ошибка самой очистки только записывается в лог и не мешает проверке.

Если ты обновляешь уже настроенный проект, **сохрани существующий `.env`** и при желании допиши туда эти три строки. Без них будут работать значения по умолчанию: 10, 20 и 50.

## Дополнительно

```bash
.venv/bin/python monitor.py --help
.venv/bin/python monitor.py --url 'https://www.altstu.ru/main/schedule/teacher/1059/'
```

Если сайт временно недоступен или возвращает `403`, программа сохраняет прежнюю базу расписания и записывает ошибку в журнал.
