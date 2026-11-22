"""Send a test message to verify the Telegram settings in .env."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from pusher.telegram_client import TelegramClient


def main() -> int:
    load_dotenv(".env")
    missing = [name for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.getenv(name)]
    if missing:
        print(f"Missing in .env: {', '.join(missing)}", file=sys.stderr)
        return 1

    client = TelegramClient(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
    client.send_message("✅ <b>Pusher</b>: hi hi hi the Telegram connection works!")
    print("Message sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
