# Dropship Agent

An "AI employee" for the research and admin side of a UK dropshipping business.
It finds and scores products and suppliers, works out true profit, drafts original
content, and shows everything in a dashboard where **you approve every real action**.

> **Safety first.** It starts in **dry-run mode**: nothing is published, emailed,
> ordered or paid for. Even in live mode, nothing happens without your click in the
> dashboard, and paid API calls stop at your monthly budget cap.

## Status

| Phase | What | Status |
|---|---|---|
| 1 | Skeleton: config, database, logging, cost cap, approvals, dashboard | ✅ Done |
| 2 | Product research: scoring, saturation penalty, compliance flags, Opportunities page | ✅ Done |
| 3 | Supplier finder (scores, red flags, email drafts, reply tracking) + profit calculator | ✅ Done |
| 4 | Competitor analyser (research only: robots.txt + terms checked first) | ✅ Done |
| 5 | Content generator (product pages, 3 ad scripts) + UK claim checker | ✅ Done |
| 6 | Video maker (9:16 MP4s with captions, music, voiceover, end card) | ✅ Done |
| 7 | Store publisher (Shopify) | Next |
| 8 | Scheduler, notifications, polish | |

---

## Setup (about 15 minutes)

### 1. Install Python
You need **Python 3.11 or newer**.
- **Windows:** download from [python.org/downloads](https://www.python.org/downloads/). In the installer, **tick "Add python.exe to PATH"**.
- **Mac:** download from [python.org/downloads](https://www.python.org/downloads/).

Check it worked: open a terminal (Windows: "Command Prompt"; Mac: "Terminal") and type
`python --version` (on Mac you may need `python3`).

### 2. Get the code
On the GitHub page for this repo click **Code → Download ZIP**, unzip it, and open a
terminal in the `dropship-agent` folder. (Or `git clone` it if you know git.)

### 3. Create a private Python environment and install
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```
You'll see `(.venv)` at the start of your terminal line. Run the `activate` line again
each time you open a new terminal.

### 4. Create your secrets file
Copy `.env.example` to a new file called `.env`. You can leave it empty for now: the
Anthropic key is only needed from phase 2. **Never share `.env`.**

### 5. Start it
```bash
python run.py init        # creates the database
python run.py demo        # adds a harmless DEMO approval so you can try the buttons
python run.py dashboard   # then open http://127.0.0.1:8000
```
Press **Ctrl+C** in the terminal to stop the dashboard.

---

## Finding products (phase 2)

1. Follow **[research/README.md](research/README.md)**: list your product ideas in
   `research/candidates.csv` and drop free Google Trends exports into `research/trends/`.
2. Run `python run.py research`, which prints a ranked list with reasons.
3. Open the dashboard → **Opportunities**. Click a product to see every number and its
   source, then **Shortlist** or **Reject** it.

How the score works: four factors (demand, trend, rough margin, competition), weighted
in `config.yaml`. A factor with no data counts as *average*, so thin data can't top the
list. Points are taken off for **saturation** (lots of advertisers, or many copies of the
same ad) and for **seasonal** products. **Data %** shows how much was actually known.
Products in your `avoid_categories` (cosmetics, toys, electrical and supplements by default)
are shown last. Anything that might need safety rules is tagged **compliance review**.

## Suppliers and profit (phase 3)

1. Add suppliers for your shortlisted products to `research/suppliers.csv`
   (see [research/README.md](research/README.md), section 4).
2. Run `python run.py suppliers`. Each supplier gets a score, red flags and the **true profit per sale**
   you'd make with them.
3. In the dashboard:
   - **Suppliers** lists every product's suppliers, best first. Click **Choose** to pick one.
   - Click a supplier for its full breakdown and an **email draft** asking the questions that matter
     (sample, UK delivery, returns, UKCA/CE, manufacturer details). Emails are **never sent for you**:
     copy one into your email, click *I've sent it*, and paste the reply when it comes. Reply speed
     feeds back into the score.
   - **Profit calculator** shows every cost line, the most you can spend on ads per sale (break-even CPA),
     break-even ROAS and a **go / marginal / no-go** verdict. Every assumption is editable and can be
     saved per product.

**The ad cost per sale (CPA) is the biggest guess in the whole sum.** The default is £10; set your own once
you've run ads. Supplier scores are a filter, not a verdict: **always order a sample first.**

## Competitor research (phase 4)

Dashboard → **Competitor reports** → paste a store's address → **Analyse** (or `python run.py competitor <url>`).

What happens, in order:
1. It reads their **robots.txt**. If it asks tools like ours to stay out, it stops.
2. It reads their **terms of service**. If they forbid automated collection ("scrape", "crawl", "spider"...),
   it stops and downloads nothing else. **Many Shopify stores use Shopify's standard terms, which say exactly
   this, so expect "not allowed" often. That's the system respecting their rules, not a bug.**
   If it can't find their terms, it asks you to read them first.
3. Only if both allow it, it politely reads their public product feed and delivery/returns pages, waiting
   3 seconds between requests and identifying itself honestly.

It stores **facts only** (price range, product count, discounts, delivery and return promises, best-seller order)
and our own summaries, never their images, descriptions or code.

When automatic collection isn't allowed, use **Add facts by hand**: browse their site yourself and fill in what
you see. Either way you get a report of **what they do well**, **gaps you could fill**, and how their prices
compare with yours. You can also paste some of their reviews to count complaint themes (the text isn't kept),
and ask for an AI summary (about 1p).

If you need this data at scale, paid store-intelligence tools license it properly; tell me and we can plug one in.

## Product pages and ad scripts (phase 5)

Needs your Anthropic API key (see **API keys** below). Each product page costs about 5p and each set of
3 ad scripts about 7p, all counted against your monthly cap.

1. Dashboard → **Content** → pick a product (ideally with a chosen supplier, so delivery times are known).
2. **Write product page** / **Write 3 ad scripts**. Claude is given only your facts (shown under
   *What Claude is told*) and strict rules: no invented specs, reviews, urgency, "best" or health claims.
   Unknown details come back as `[CHECK: ...]` placeholders for you to fill in.
3. Every draft goes through the **claim checker**. Risky phrases are highlighted:
   - **must fix** (red): fake reviews or ratings, fake urgency, health or safety claims without documents,
     "no returns", delivery promises faster than your supplier, unfilled `[CHECK]` placeholders.
     **Approve stays locked until these are gone.**
   - **need evidence** (amber): "best" claims, "was" prices, eco claims, guarantees, absolute claims.
4. Edit the text, **Save and re-check**, then **Approve**. Once a page and at least one ad script are approved,
   the product moves to **content ready**.

Ad angles: problem → solution, social proof *without* fake reviews (leaves a slot for a real customer clip
if you ever have one, with permission), and demo. Each has a 3-second hook, voiceover, call to action,
on-screen text, a shot list and TikTok / Reels / Shorts notes.

`brand.yaml` sets the voice, and what you really offer (returns window, free delivery, any guarantee).
The checker uses it: "free delivery" is only allowed if you've set `free_delivery: true`.

The claim checker catches common problems; it isn't legal advice. Read everything before approving.

## Videos (phase 6)

Turns **approved** ad scripts into 1080×1920 (9:16) MP4s for TikTok, Reels and Shorts. Free: it uses ffmpeg
on your computer.

**Install ffmpeg once:**
- **Windows:** open PowerShell and run `winget install Gyan.FFmpeg`, then close and reopen your terminal.
- **Mac:** install [Homebrew](https://brew.sh), then run `brew install ffmpeg`.
- Check it worked: `ffmpeg -version`.

**Then:**
1. Film a few short clips of the product (or get permission from the supplier to use theirs). 3-6 different
   shots work best. Phone footage is fine: landscape or portrait, it's cropped to fill the frame.
2. Put them in `media/<product-folder>/` (the **Videos** page lists the folder names). Optional:
   royalty-free music in `media/music/`, and your own voiceover saved as `media/<product-folder>/voice-demo.mp3`
   (or `voice-problem_solution.mp3`, `voice-social_proof.mp3`).
3. Dashboard → **Videos** → record each file's **licence** (filmed by you / supplier permission / royalty-free,
   plus a note of where the proof is). **Files without a licence are never used.**
4. Click **Render** next to an approved script (about 10-20 seconds each), watch it, then **Approve** or **Reject**.
   Or run `python run.py video` to render all approved scripts at once.

Each video gets: shots cut every few seconds, the hook as a big caption in the first 3 seconds, the on-screen
lines and call to action as captions, optional music (quiet, fades out) and voiceover, and an end card with
your store name. Videos are saved in `output/videos/`.

Never faked: no invented customers, unboxings or testimonials. Script lines that were placeholders
(like a "real customer clip" you don't have) are left out, and you're told.

Paid AI voices or AI video can be plugged in later (one small class each, with cost tracking and approval).
Tell me which provider you'd like once you have an account, and I'll add and test it.

## Commands

| Command | What it does |
|---|---|
| `python run.py init` | Create the database and folders |
| `python run.py status` | Show mode, budget used, and pending approvals |
| `python run.py dashboard` | Open the dashboard at http://127.0.0.1:8000 |
| `python run.py demo` | Add a clearly-labelled demo approval request |
| `python run.py research` | Score and rank your product ideas (`--explain 3` adds Claude summaries for the top 3: paid, a few pence) |
| `python run.py suppliers` | Score suppliers and show true profit with each (`--draft-emails` drafts enquiries, never sends) |
| `python run.py profit --price 19.99 --cost 4 --shipping 3` | Quick profit check with your default assumptions |
| `python run.py competitor https://store.co.uk` | Research a competitor (facts only; add `--terms-checked` if you've read their terms and we couldn't find them) |
| `python run.py content` | Write a page + 3 ad scripts for products with a chosen supplier (`--product NAME`, `--what page|ads|all`; paid) |
| `python run.py video` | Render all approved ad scripts into MP4s (`--product NAME`, `--music track.mp3`, `--voice recorded`) |
| `python run.py publish / all` | Arrive in later phases |
| `python run.py --dry-run <command>` | Force dry-run for this command, whatever config says |

## Settings you might change (`config.yaml`)

- `dry_run`: `true` (safe) or `false` (approved actions really happen).
- `budget.monthly_cap_gbp`: hard monthly limit for paid API calls (default **£25**).
- `budget.warn_at_fraction`: warning level (default **0.8** = 80%).
- `budget.approval_threshold_gbp`: any single paid call estimated above this needs your approval (default **£1**).
- `llm.default_model` / `llm.task_models`: which Claude model to use, per task if you like.
- `business.vat_registered`: set `true` once you register for VAT.
- `profit`: default costs and assumptions (card fees, Shopify fee, refund rate, ad cost per sale...). **Check these against your Shopify plan.**
- `suppliers`: scoring weights, the 7-day delivery target and the 15-day slow-shipping flag.
- `competitors`: how politely we fetch (user agent, seconds between requests, cache time).
- `video`: size, shot length, caption font and size, brand colours, music volume, and the ffmpeg location.

Your brand voice for product pages and ads lives in `brand.yaml`.

## API keys

**Anthropic (Claude): needed for content (phase 5); optional for research and competitor summaries:**
1. Go to [console.anthropic.com](https://console.anthropic.com) and sign up.
2. Add a payment method under **Billing** and, to be safe, set a low monthly spend limit there too.
3. Go to **API Keys → Create Key**, and copy it.
4. Open `.env` and paste it after `ANTHROPIC_API_KEY=` (no spaces or quotes).

**Meta Ad Library (optional):** see [research/README.md](research/README.md).

Other keys (CJ Dropshipping, Shopify, ...) will be explained when their phase arrives.

## How the safety pieces work

- **Approval queue:** modules never act directly. They create a request; you approve
  or reject it in the dashboard; only approved requests can run, and only once.
- **Dry-run:** approved requests are just logged ("would have done X") until you
  set `dry_run: false`.
- **Budget guard:** before every paid call the worst-case cost is estimated. If it
  would break the monthly cap, the call is blocked. A warning appears at 80%.
- **Cost logging:** every paid call is recorded with its real cost (Logs & costs page).
- **Caching:** asking the AI the exact same thing twice costs nothing the second time.
- **No made-up data:** if a source returns nothing, the app says so. Every metric
  stores where it came from and when.
- **Local only:** the dashboard only listens on your own computer (127.0.0.1).

## Running the tests
```bash
python -m pytest -q
```
Tests use a temporary database and fake API responses, so they cost nothing.

## Project layout
```
dropship-agent/
  app/core/        config, database, logging, cost tracker, approvals, Claude wrapper
  app/api/         the web app (dashboard pages + /api/status)
  app/dashboard/   HTML templates and styles
  app/modules/     one folder per job (added phase by phase)
  tests/           automated tests
  media/           your footage, music and voiceovers (not committed)
  output/          videos and reports (not committed)
  config.yaml      settings (safe to commit)
  brand.yaml       brand voice
  .env.example     template for your secrets
  run.py           the command line
```
Your database, logs and AI cache live in `data/` and `logs/`. They stay on your
computer and are never committed.
