# auto-job-application

A free bot that searches the web for job openings that match your profile. When it finds a company's HR or careers email, it sends one application email (with your PDF resume attached) per interval. Every opening it finds is also saved, so you can apply manually on job boards such as LinkedIn and Naukri. A password-protected dashboard tracks all of this and lets you edit your job profile and sending settings.

It runs on **Vercel** (free Hobby plan) with a **Neon Postgres** database (free tier), or locally with SQLite.

## Deploy to Vercel

1. **Push this repo to GitHub** and import it in Vercel (Add New → Project). Vercel detects the Flask app in `app.py`; leave the framework preset as detected (Flask) or Other, with no build settings.
2. **Add the database.** In the project, go to Storage → Create Database → **Neon** (free) and connect it. This sets `DATABASE_URL` for you. The tables are created automatically on first use.
3. **Add environment variables** (Settings → Environment Variables):

   | Variable | Value |
   |---|---|
   | `GMAIL_USER` | the Gmail address applications are sent from |
   | `GMAIL_APP_PASSWORD` | a Gmail **App Password** (Google Account → Security → 2-Step Verification → App passwords), not your normal password |
   | `DASHBOARD_PASSWORD` | the password for the dashboard login |
   | `CRON_SECRET` | any long random string, e.g. the output of `openssl rand -hex 32`. Vercel sends it to `/api/run`, and calls without it are rejected |
   | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_API_KEY` | optional free job APIs |

4. **Redeploy** so the variables take effect, then open the site and log in.
5. In the dashboard's **Settings** tab, upload your resume PDF and set **Test recipient** to your own email. Press **Run now**. One application should arrive in your inbox with the resume attached and the real recipient shown in the subject. When you're happy, clear the test recipient.

That's it. The schedule in `vercel.json` (`job-applier`, every 15 minutes) appears under the project's **Schedules** page. On each run the bot sends an application only if your interval (default 60 minutes) has passed since the last one, and only within active hours and under the daily limit.

## How it works

1. **Search** (all free): DuckDuckGo web search needs no key. Remotive is used for remote jobs, Arbeitnow is optional, and Adzuna and Jooble are used if you add their keys.
2. **Match**: keyword overlap with your job titles and skills, a filter that skips postings whose experience range doesn't overlap yours, title words to exclude, and work mode (onsite/hybrid/remote).
3. **Save** every matching opening, so it appears in the dashboard's Job openings tab.
4. **Find an email**: first from the posting text, then by crawling the company website's careers, contact and about pages. The bot prefers `hr@`, `careers@`, `jobs@` and recruiting addresses.
5. **Send** one email through Gmail SMTP and record it. A company is never emailed twice.

Each run is limited to about 3 minutes, under Vercel's 300-second function limit. If a run doesn't find a lead with an email in that time, the next run carries on from the same queue.

## Dashboard

- **Overview**: applications emailed, sent today against the daily limit, openings found, applied manually, failed sends, a 14-day chart, the last run's result and when the next application is due.
- **Email applications**: every email sent, with search, a status filter and CSV download.
- **Job openings**: every matching opening, with a link, experience, work mode and a short description. Apply on the site, then click **Mark applied**. CSV download is available too.
- **Email template**: subject, body and your details (name, phone, LinkedIn, experience), with placeholders and a live preview.
- **Job profile**: job description, titles, keywords, excluded words, experience range, locations, work mode and match strictness.
- **Settings**: a setup checklist, interval, daily limit, test recipient, active hours and resume upload.
- **Bot log**: the latest lines from the bot's runs.
- Buttons: **Pause/Resume** (scheduled runs do nothing while paused), **Run now** (sends one application immediately, ignoring the interval) and **Dry run** (does everything except send).

## Configuration files

| File | What it holds |
|---|---|
| `config.yaml` | Timezone, search sources, and the *defaults* for sending settings and the email template |
| `job_profile.yaml` | The default job profile. It is copied into the database on the first run; after that, edit it in the dashboard |
| `vercel.json` | Vercel function settings and the 15-minute schedule |

The job profile, email template, your contact details, sending settings and resume are all edited in the dashboard and stored in the database, so they never need a commit. Keep personal details out of `config.yaml` if the repo is public. Other changes to `config.yaml` (timezone, search sources) take effect after you push and Vercel redeploys.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env              # fill in GMAIL_USER and GMAIL_APP_PASSWORD
python dashboard.py               # http://127.0.0.1:5000
python main.py                    # in another terminal: runs the bot every 15 min
python main.py --dry-run --once   # one run that logs the email instead of sending it
```

Without `DATABASE_URL` the data goes to `data/jobbot.db` (SQLite). To work against your Neon database locally instead, put its connection string in `.env` as `DATABASE_URL`. Locally the dashboard has no login unless you set `DASHBOARD_PASSWORD`.

## Notes and limits

- Free web search engines rate-limit cloud IPs, Vercel's included. When DuckDuckGo stops answering, the bot skips it for that round. Remotive, Adzuna and Jooble (both free with a key) make the search more reliable.
- The bot does not log in to LinkedIn or Naukri or fill in their forms. Those openings are listed in the dashboard for you to apply manually.
- Keep the daily limit modest and the email personal. Bulk unsolicited email can get a Gmail account flagged.

## Tests

```bash
python -m pytest -q
# include the Postgres tests:
TEST_DATABASE_URL=postgresql://user:pass@host/db python -m pytest -q
```
