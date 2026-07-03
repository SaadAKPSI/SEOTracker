# Alpha Kappa Psi — Mention Tracker

A **100% free**, API-key-free dashboard that automatically monitors the web for
mentions of **"Alpha Kappa Psi"**, stores them in a JSON dataset, and displays
them on a live GitHub Pages site. It runs indefinitely at zero cost using
GitHub Actions and free RSS feeds (Google Alerts + Google News).

## How it works

```
Google Alerts RSS / Google News RSS
              │
              ▼
   scripts/fetch_mentions.py   (runs every 30 min via GitHub Actions)
              │  filter → sentiment → dedup → append
              ▼
        data/mentions.json      (growing dataset, committed back to repo)
              │
              ▼
   dashboard/index.html         (served by GitHub Pages)
```

- **No paid APIs.** Only free RSS feeds — no NewsAPI, SerpAPI, or Bing.
- **No API keys.** Nothing to configure beyond pasting your RSS URL.
- **Free forever.** GitHub Actions + GitHub Pages cover public repos at no cost.

## Repository structure

```
akpsi-dashboard/
├── data/
│   └── mentions.json          # growing dataset (deduplicated by URL)
├── scripts/
│   └── fetch_mentions.py      # RSS fetcher / filter / sentiment / dedup
├── dashboard/
│   └── index.html             # vanilla HTML/CSS/JS dashboard
├── .github/workflows/
│   └── update.yml             # cron: every 30 minutes
├── requirements.txt
└── README.md
```

---

## Setup instructions

### 1. Create the repository

1. Create a **public** GitHub repo (public repos get free unlimited Actions +
   Pages).
2. Upload these files, keeping the folder structure above. Commit to `main`.

### 2. Create a Google Alerts RSS feed (recommended primary source)

1. Go to <https://www.google.com/alerts>.
2. In the search box type the exact phrase with quotes: `"Alpha Kappa Psi"`.
3. Click **Show options** and set:
   - **How often:** As-it-happens
   - **Sources:** Automatic
   - **Deliver to:** **RSS feed**  ← this is the important part
4. Click **Create Alert**.
5. On the *Manage Alerts* page, click the **RSS (orange) icon** next to your new
   alert and copy the URL. It looks like:
   `https://www.google.com/alerts/feeds/1234567890/0987654321`

> A Google Alert with "Deliver to: RSS feed" requires no API key and is free.

### 3. Add your RSS URL to the script

Open `scripts/fetch_mentions.py` and paste your feed(s) into the `FEEDS` list:

```python
FEEDS = [
    "https://www.google.com/alerts/feeds/1234567890/0987654321",  # your alert
    "https://news.google.com/rss/search?q=%22Alpha+Kappa+Psi%22&hl=en-US&gl=US&ceid=US:en",
]
```

You can add any number of free public RSS feeds to this list. A working Google
News RSS feed is already included, so the system produces results even before you
add your own alert.

> Alternatively, set the `AKPSI_FEEDS` environment variable (comma-separated
> URLs) to override the list without editing code.

### 4. Enable GitHub Actions

1. Go to the repo's **Actions** tab and enable workflows if prompted.
2. Ensure **Settings → Actions → General → Workflow permissions** is set to
   **Read and write permissions** (needed so the bot can commit updated data).
3. Trigger the first run manually: **Actions → Update AKPsi Mentions → Run
   workflow**. After it finishes, `data/mentions.json` will be updated.

The workflow then runs automatically every 30 minutes. (GitHub may delay
scheduled runs slightly during peak load — this is normal and free.)

### 5. Enable GitHub Pages

1. Go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **Deploy from a branch**.
3. Select branch **`main`** and folder **`/ (root)`**, then **Save**.
4. Your dashboard will be live at:
   `https://<your-username>.github.io/<repo-name>/dashboard/`

The dashboard reads `data/mentions.json` directly, so it stays current every time
the workflow commits new mentions.

---

## Local testing (optional)

```bash
pip install -r requirements.txt
python scripts/fetch_mentions.py          # updates data/mentions.json
python -m http.server 8000                # then open:
# http://localhost:8000/dashboard/
```

---

## Data format

`data/mentions.json` is an array of objects:

```json
[
  {
    "title": "Alpha Kappa Psi chapter raises $12,000 for local food bank",
    "source": "Campus Times",
    "date": "2026-06-30T14:20:00+00:00",
    "url": "https://example.com/news/akpsi-food-bank-fundraiser",
    "summary": "The Alpha Kappa Psi business fraternity completed a philanthropy drive...",
    "sentiment": "positive"
  }
]
```

## How filtering works

- **Exact phrase** `Alpha Kappa Psi` (case- and whitespace-insensitive) always
  qualifies.
- **`AKPsi`** qualifies **only** when fraternity context words are present
  (e.g. *fraternity, chapter, pledge, rush, greek, brotherhood, initiation*),
  which avoids unrelated abbreviations.
- **Deduplication** is by canonical URL (Google redirect wrappers are unwrapped).
- Only **new** URLs are appended; existing entries are never overwritten.

## Sentiment

A lightweight, dependency-free keyword heuristic tags each mention as
`positive`, `neutral`, or `negative` by tallying positive words (award, honor,
scholarship, philanthropy…) against negative ones (hazing, lawsuit, suspended…).

## Optional improvements

- **Weekly email digest** via a second scheduled workflow.
- **More feeds:** add other schools' or chapters' Google Alerts to `FEEDS`.
- **Smarter sentiment** by swapping the heuristic for a small local model
  (still free, no API).
- **Charts:** add a sentiment-over-time chart to the dashboard.
- **Archiving:** the script already caps the dataset at 1,000 entries
  (`MAX_ENTRIES`) to prevent file bloat — adjust as needed.

## Cost

| Component        | Service         | Cost |
|------------------|-----------------|------|
| Data fetching    | GitHub Actions  | Free (public repo) |
| Hosting          | GitHub Pages    | Free |
| Data sources     | Google Alerts / News RSS | Free |
| **Total**        |                 | **$0** |

## License

MIT — do whatever you like.
