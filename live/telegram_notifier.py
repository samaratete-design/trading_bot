"""
live/telegram_notifier.py

Minimal, one-way Telegram sender. No commands, no polling, no inline
keyboards -- per "no unnecessary infrastructure" for this phase. Just
sends text notifications.

Secrets are read from environment only, never hard-coded:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

If either is missing, the notifier degrades to printing to stdout instead
of raising -- a missing Telegram config should not crash the paper bot.
"""
from __future__ import annotations

import os

import requests


class TelegramNotifier:
    def __init__(self, token: str = None, chat_id: str = None) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token) and bool(self.chat_id)
        if not self.enabled:
            print("[telegram] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set -- "
                  "notifications will print to stdout only.")

    def send(self, text: str) -> bool:
        print(text)
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=10)
            if resp.status_code != 200:
                print(f"[telegram] send failed ({resp.status_code}): {resp.text}")
                return False
            return True
        except Exception as e:
            print(f"[telegram] send exception: {e}")
            return False
