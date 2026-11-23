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
   - threads (the account replying to its own tweet) are merged into one
     tweet: the text of all its tweets, separated by blank lines, and all their
     media. A thread is forwarded when any of its tweets matches (e.g. only the
     first one tags you). Replies by other people are never included. Turn this
     off with `INCLUDE_THREADS=false`
   - with `FORWARD_MY_TWEETS=true`, your own account (`TWITTER_MY_USERNAME`),
     keeping all your tweets, plus your retweets when
     `FORWARD_MY_RETWEETS=true`
2. Each of those tweets is forwarded to `TELEGRAM_CHAT_ID`. Retweets are sent
   with the full text and media of the original tweet:
   - no media → a text message, with the preview card of its first link (e.g. an article's image and title)
   - one photo / video / GIF → `sendPhoto` / `sendVideo` / `sendAnimation` with the text as caption
   - several media → an album (`sendMediaGroup`)
   - text over Telegram's 1024-character caption limit → the text goes in its own message, followed by the media
   - Telegram's own limits can't be avoided for long threads: text over 4096
     characters is split into several messages (between tweets where possible),
     and more than 10 media items are sent as several albums
3. Media is first sent by URL. If Telegram can't fetch it (Telegram fetches files
   up to 20 MB by URL), the bot downloads the file and uploads it (up to 50 MB).
   If that fails too, it sends the media as links.
4. Edited tweets: on X every edit is a new tweet. When the original and its
   edits come in the same check, only the newest version is sent. An edit that
   comes after the original was already sent updates that Telegram message
   instead: the text (or caption) is edited in place, or, if the media changed
   or the text no longer fits one message, the old messages are deleted and the
   new version is sent in their place.
5. The id of the last processed tweet of each account is saved to `STATE_FILE`,
   so restarting doesn't send duplicates, together with the Telegram messages
   of the last 200 forwarded tweets, so their edits can update them. On the
   first run for an account the bot only records a starting point and doesn't
   forward older tweets.

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
- For each new tweet that starts a thread (it has replies), the scraper also
  opens the tweet's page to read the rest of the thread.
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
| `INCLUDE_THREADS` | no | `true` | Send a thread as one message with the rest of its tweets |
| `CREDIT_PREFIXES` | no | | Comma-separated; a line that starts with one of these is removed from the message, with the rest of its paragraph (see [Removing credits](#removing-credits)) |
| `STATE_FILE` | no | `state.json` | Where the last processed tweet id is stored |
| `LOG_LEVEL` | no | `INFO` | Logging level |

\* Required unless `TWITTER_MY_USERNAME` is set, which is then used as the only entry.

### Removing credits

Some tweets end with a credit, such as the show and its host, that you may not
want in the message. List how those lines start in `CREDIT_PREFIXES`:

```
CREDIT_PREFIXES=#מהדורתכאןחדשות,כאן חדשות ברשת ב'
```

Each line that starts with one of the prefixes (ignoring case and leading
spaces) is removed, together with the following lines of its paragraph, up to
the next blank line. So for

```
... | @yaara_shapira עם הפרטים

כאן חדשות ברשת ב' 🎙️| #הבוקרהזה
@yaara_shapira

https://www.kan.org.il/...
```

the second paragraph is removed, while the tag in the first one and the link
are kept. Only the message text changes: which tweets are forwarded still
depends on all their tags.

### API source notes

- X bills the API per post returned. The search query only matches tweets of the
  watched account that tag one of `TWITTER_MENTIONED_USERNAMES`, so you pay only
  for tweets that are forwarded, not for everything the account posts. Empty
  checks return no posts. With `FORWARD_MY_TWEETS`, each of your own tweets is
  billed as well, and a retweet may also bill the original tweet it includes.
- With `INCLUDE_THREADS` (the default), one extra search per check loads the
  rest of the threads started by the forwarded tweets
  (`conversation_id:<id> from:<account> is:reply`, up to 10 threads per
  search). Their tweets, and the account's replies to other people in those
  conversations, are billed too.
- A thread is read when its first tweet is forwarded. Tweets added to it after
  that check come in later as separate messages; for the watched account, only
  if they tag you themselves.
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

## Deploying to a server

`.github/workflows/deploy.yml` runs the tests on every push and pull request.
On a push to `main` that passes them, it connects to your server over SSH,
pulls the code and runs `scripts/deploy.sh`, which rebuilds the image and
replaces the container. The state volume is kept, so nothing is sent twice.
Until the SSH secrets below are set, the deploy step is skipped.

One-time server setup (any Linux VM, e.g. a Google Cloud `e2-micro`):

1. Install Docker and git, and let your user run Docker:
   ```bash
   sudo apt update && sudo apt install -y docker.io git
   sudo usermod -aG docker $USER   # log out and back in afterwards
   ```
2. Clone the repository into `~/pusher`. For a private repository, create a
   read-only deploy key (`ssh-keygen -t ed25519 -f ~/.ssh/pusher_repo`), add the
   `.pub` file under the repository's **Settings → Deploy keys**, and clone with
   `GIT_SSH_COMMAND="ssh -i ~/.ssh/pusher_repo" git clone git@github.com:<owner>/<repo>.git ~/pusher`,
   then run `git -C ~/pusher config core.sshCommand "ssh -i ~/.ssh/pusher_repo"`.
3. Copy your `.env` to the server and restrict it (it never goes through GitHub):
   ```bash
   scp .env <user>@<server-ip>:~/pusher/.env
   ssh <user>@<server-ip> chmod 600 ~/pusher/.env
   ```
   Leave out `X_AUTH_TOKEN` unless you use the scraper: it grants full access
   to the X account.
4. Create a key pair for GitHub Actions on your own machine,
   `ssh-keygen -t ed25519 -f pusher_deploy -N ""`, and append
   `pusher_deploy.pub` to `~/.ssh/authorized_keys` on the server.
5. In the repository's **Settings → Secrets and variables → Actions**, add the
   secrets `SSH_HOST` (server IP), `SSH_USER` and `SSH_KEY` (the contents of
   the private `pusher_deploy` file). If the project isn't in `~/pusher`, add a
   variable `DEPLOY_PATH` with its path relative to the home directory.
6. Stop any other running copy of the bot (e.g. `docker stop pusher` on your
   computer), otherwise every tweet is sent twice. Then push to `main`, or run
   the workflow from the **Actions** tab, to deploy.

To change `.env` later, edit it on the server and run `sh scripts/deploy.sh` there.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
