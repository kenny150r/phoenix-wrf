#!/bin/bash
# Install systemd --user timer. Linger requires sudo (reported if it fails).
set -euo pipefail
ROOT="/home/kenny/phoenix-wrf"
UNITDIR="$HOME/.config/systemd/user"
mkdir -p "$UNITDIR" "$ROOT/data/logs"
cp -f "$ROOT/systemd/phoenix-wrf.service" "$UNITDIR/"
cp -f "$ROOT/systemd/phoenix-wrf.timer" "$UNITDIR/"
systemctl --user daemon-reload
systemctl --user enable --now phoenix-wrf.timer
systemctl --user status phoenix-wrf.timer --no-pager || true
echo "Next trigger:"
systemctl --user list-timers phoenix-wrf.timer --no-pager || true

if loginctl show-user kenny -p Linger 2>/dev/null | grep -q Linger=yes; then
  echo "linger already enabled"
else
  if sudo -n loginctl enable-linger kenny 2>/dev/null; then
    echo "linger enabled"
  else
    echo "BLOCKER: enable linger so the timer runs when not logged in:"
    echo "  sudo loginctl enable-linger kenny"
  fi
fi
