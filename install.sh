#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd -P)"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [[ ! -f .env ]]; then
    cp .env.example .env
    chmod 600 .env
fi
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/schedule-monitor.service" <<SERVICE
[Unit]
Description=Schedule Monitor
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python $ROOT/monitor.py
SERVICE
cp systemd/schedule-monitor.timer "$HOME/.config/systemd/user/schedule-monitor.timer"
systemctl --user daemon-reload
echo 'Установка завершена.'
echo 'Проверьте .env, затем запустите:'
echo 'systemctl --user enable --now schedule-monitor.timer'
