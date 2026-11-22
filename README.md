# Pusher

Watches one X (Twitter) account and sends you a Telegram message every time that
account posts a tweet that tags you. The message has the tweet's text, a link to
the tweet, and its photos, videos and GIFs.

## How it works

1. Every `POLL_INTERVAL_SECONDS`, it fetches the watched account's new tweets
   through the X API v2 user timeline endpoint.
2. Each tweet whose mentions include `TWITTER_MY_USERNAME` is forwarded to
   `TELEGRAM_CHAT_ID`:
   - no media → a text message
   - one photo / video / GIF → `sendPhoto` / `sendVideo` / `sendAnimation` with the text as caption
   - several media → an album (`sendMediaGroup`)
   - text over Telegram's 1024-character caption limit → the text goes in its own message, followed by the media
3. Media is first sent by URL. If Telegram can't fetch it (Telegram fetches files
   up to 20 MB by URL), the bot downloads the file and uploads it (up to 50 MB).
   If that fails too, it sends the media as links.
4. The id of the last processed tweet is saved to `STATE_FILE`, so restarting
   doesn't send duplicates. On the very first run the bot only records the
   newest tweet and doesn't forward older ones.

## Setup

1. **X API**: create a project and app at <https://developer.x.com> and copy its
   Bearer Token. Reading timelines needs a paid access tier (Basic or higher).
2. **Telegram bot**: create a bot with [@BotFather](https://t.me/BotFather) and
   copy the token. Send your bot any message, then get your chat id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates` (`message.chat.id`).
3. Copy `.env.example` to `.env` and fill in the values.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `TWITTER_BEARER_TOKEN` | yes | | X API v2 bearer token |
| `TWITTER_WATCHED_USERNAME` | yes | | Account to watch (with or without `@`) |
| `TWITTER_MY_USERNAME` | yes | | Your handle; tweets that tag it are forwarded |
| `TELEGRAM_BOT_TOKEN` | yes | | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | | Chat that receives notifications |
| `POLL_INTERVAL_SECONDS` | no | `300` | Seconds between checks |
| `INCLUDE_RETWEETS` | no | `false` | Also check the account's retweets |
| `INCLUDE_REPLIES` | no | `true` | Also check the account's replies |
| `STATE_FILE` | no | `state.json` | Where the last processed tweet id is stored |
| `LOG_LEVEL` | no | `INFO` | Logging level |

Keep the poll interval within your X API tier's rate limits. If the API returns
429, the bot waits for the rate-limit window to reset and then continues.

## Running

```bash
pip install -r requirements.txt
python -m pusher            # run continuously
python -m pusher --once     # single check, e.g. from cron
```

With Docker:

```bash
docker build -t pusher .
docker run -d --name pusher --env-file .env -v pusher-data:/data pusher
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
