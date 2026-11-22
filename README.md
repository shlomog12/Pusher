# Pusher

Watches one X (Twitter) account and sends you a Telegram message every time that
account posts a tweet that tags you, or any other account in a list you choose.
It can also forward every tweet you post yourself, optionally with your retweets.
The message is just the tweet's text with its photos, videos and GIFs, as if
someone had sent it to you directly.

## How it works

1. Every `POLL_INTERVAL_SECONDS`, it loads new tweets through the configured
   tweet source (see [Tweet sources](#tweet-sources)) for each account it checks:
   - the watched account (`TWITTER_WATCHED_USERNAME`), keeping only tweets that
     tag one of `TWITTER_MENTIONED_USERNAMES`
   - replies are skipped for both unless `INCLUDE_REPLIES=true`
   - with `FORWARD_MY_TWEETS=true`, your own account (`TWITTER_MY_USERNAME`),
     keeping all your tweets, plus your retweets when
     `FORWARD_MY_RETWEETS=true`
2. Each of those tweets is forwarded to `TELEGRAM_CHAT_ID`. Retweets are sent
   with the full text and media of the original tweet:
   - no media → a text message
   - one photo / video / GIF → `sendPhoto` / `sendVideo` / `sendAnimation` with the text as caption
   - several media → an album (`sendMediaGroup`)
   - text over Telegram's 1024-character caption limit → the text goes in its own message, followed by the media
3. Media is first sent by URL. If Telegram can't fetch it (Telegram fetches files
   up to 20 MB by URL), the bot downloads the file and uploads it (up to 50 MB).
   If that fails too, it sends the media as links.
4. The id of the last processed tweet of each account is saved to `STATE_FILE`,
   so restarting doesn't send duplicates. On the first run for an account the
   bot only records a starting point and doesn't forward older tweets.

## Tweet sources

Tweets are loaded through the `TweetSource` abstract class
(`pusher/sources/base.py`). `TweetSourceFactory` (`pusher/sources/factory.py`)
creates the implementation named by `TWEET_SOURCE`, so switching between them
only takes a change in `.env`.

| `TWEET_SOURCE` | Class | How it loads tweets | Needs |
| --- | --- | --- | --- |
| `api` (default) | `ApiTweetSource` | Official X API v2 recent search for `from:<watched> @<you>` | A bearer token with pay-per-use credits |
| `scraper` | `ScraperTweetSource` | Headless Chromium (Playwright) opens the profile on x.com and reads the GraphQL timeline responses the page loads | The `auth_token` cookie of a logged-in X account |

### Scraper notes

- X only shows timelines to logged-in users, so the scraper uses a session
  cookie. Log in to x.com in a browser, open dev tools → Application → Cookies →
  `https://x.com`, and copy `auth_token` (optionally `ct0` too) into `.env`.
  Use a secondary account: scraping is against X's terms of service and the
  account may be rate limited or suspended. The cookie expires when you log out.
- Each check reads the most recent tweets on the profile, plus
  `SCRAPER_SCROLLS` scrolls. If the account posts more tweets between two checks
  than that covers, increase `SCRAPER_SCROLLS` or shorten the poll interval.
- With `FORWARD_MY_TWEETS`, each check loads your profile too, in a second
  browser session.
- Install the extra dependencies: `pip install -r requirements-scraper.txt`
  and `playwright install chromium`.

### Adding a source

Subclass `TweetSource`, implement `initial_since_id()` and `new_tweets()`, and
register it:

```python
TweetSourceFactory.register("rss", RssTweetSource.from_config)
```

## Setup

1. Choose a tweet source and get its credentials (see above). For the API, create
   a project and app at <https://developer.x.com> and copy its Bearer Token.
2. **Telegram bot**: create a bot with [@BotFather](https://t.me/BotFather) and
   copy the token. Send your bot any message, then get your chat id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates` (`message.chat.id`).
3. Copy `.env.example` to `.env` and fill in the values.
4. Check the Telegram settings by sending a test message:
   `python -m scripts.check_telegram`

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `TWEET_SOURCE` | no | `api` | `api` or `scraper` |
| `TWITTER_WATCHED_USERNAME` | yes | | Account to watch (with or without `@`) |
| `TWITTER_MENTIONED_USERNAMES` | yes* | `TWITTER_MY_USERNAME` | Comma-separated accounts; watched tweets that tag any of them are forwarded |
| `TWITTER_MY_USERNAME` | with `FORWARD_MY_TWEETS` | | Your handle |
| `FORWARD_MY_TWEETS` | no | `false` | Also forward every tweet you post |
| `FORWARD_MY_RETWEETS` | no | `false` | With `FORWARD_MY_TWEETS`, also forward your retweets |
| `TELEGRAM_BOT_TOKEN` | yes | | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | | Chat that receives notifications |
| `TWITTER_BEARER_TOKEN` | with `api` | | X API v2 bearer token |
| `X_AUTH_TOKEN` | with `scraper` | | `auth_token` cookie of a logged-in x.com session |
| `X_CT0` | no | | `ct0` cookie of the same session |
| `SCRAPER_HEADLESS` | no | `true` | Run the browser without a window |
| `SCRAPER_SCROLLS` | no | `2` | Profile scrolls per check |
| `SCRAPER_TIMEOUT_SECONDS` | no | `45` | Page load timeout |
| `SCRAPER_BROWSER_PATH` | no | | Chrome/Chromium binary to use instead of Playwright's |
| `POLL_INTERVAL_SECONDS` | no | `300` | Seconds between checks |
| `INCLUDE_RETWEETS` | no | `false` | Also check the watched account's retweets |
| `INCLUDE_REPLIES` | no | `false` | Also forward replies, from the watched account and from you |
| `STATE_FILE` | no | `state.json` | Where the last processed tweet id is stored |
| `LOG_LEVEL` | no | `INFO` | Logging level |

\* Required unless `TWITTER_MY_USERNAME` is set, which is then used as the only entry.

### API source notes

- X bills the API per post returned. The search query only matches tweets of the
  watched account that tag one of `TWITTER_MENTIONED_USERNAMES`, so you pay only
  for tweets that are forwarded, not for everything the account posts. Empty
  checks return no posts. With `FORWARD_MY_TWEETS`, each of your own tweets is
  billed as well, and a retweet may also bill the original tweet it includes.
- The first run needs no API reads: the starting point is a tweet id computed
  from the current time.
- Recent search covers the last 7 days. If the bot was down for longer than
  that, it resumes from the oldest point the endpoint allows, so tweets older
  than 7 days are skipped.
- If the API returns 429 (rate limit), the bot waits for the rate-limit window
  to reset and then continues.

## Running

```bash
pip install -r requirements.txt           # API source
pip install -r requirements-scraper.txt   # scraper source
playwright install chromium               # scraper source

python -m pusher            # run continuously
python -m pusher --once     # single check, e.g. from cron
```

With Docker:

```bash
docker build -t pusher .                                  # API source
docker build -t pusher --build-arg WITH_SCRAPER=true .    # also supports the scraper
docker run -d --name pusher --env-file .env -v pusher-data:/data pusher
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
