# Dropship Agent

An "AI employee" for the research and admin side of a UK dropshipping business. It finds and
scores products, compares suppliers, works out your **true profit per sale**, researches
competitors (facts only), writes original product pages and ad scripts, renders short vertical
video ads, and lists products on Shopify. Everything appears in a dashboard where **you approve
every real action**.

> **Safety first.** It starts in **dry-run mode**: nothing is published, emailed, ordered or paid
> for, only logged. Even in live mode, nothing happens without your click, and paid AI calls stop
> at your monthly budget cap (£25 by default).

**What it won't do:** copy competitors' text or images, scrape sites that forbid it, invent
data, reviews or urgency, send emails to suppliers, place orders, run ads, or publish anything
without your approval.

---

## Quick start

**On Windows, follow [GO-LIVE-WINDOWS.md](GO-LIVE-WINDOWS.md)**: install Python 3.13, double-click
`setup.bat`, then `start.bat`. It covers everything from download to a live Shopify store and Meta ads.

**On a Mac (or by hand):**
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python run.py doctor               # what's ready, what's missing, and how to fix it
python run.py init
python run.py dashboard --open     # http://127.0.0.1:8000  (Ctrl+C to stop)
```
Use **Python 3.13** (tested). Put your store name in `brand.yaml`. The **Setup checklist** page shows what each
feature still needs.

---

## Your first product, step by step

| Step | Where | Needs | Cost |
|---|---|---|---|
| 1. List 5-20 product ideas + Google Trends downloads | `research/` folder ([how](research/README.md)) | nothing | free |
| 2. Score and rank them | `python run.py research` → **Opportunities** → *Shortlist* | | free |
| 3. Check competitors (optional) | **Competitor reports** | | free |
| 4. Add 2-5 suppliers per shortlisted product | `research/suppliers.csv` → `python run.py suppliers` | | free |
| 5. Check true profit, adjust your assumptions, choose a supplier | **Suppliers**, **Profit calculator** | | free |
| 6. Email suppliers (drafts written for you, sent by you) and **order a sample** | **Suppliers** → a supplier | | sample cost |
| 7. Write the product page + 3 ad scripts, fix flagged claims, approve | **Content** | Anthropic key | ~12p |
| 8. Film the sample (3-6 short clips), record licences, render videos | **Videos** | ffmpeg | free |
| 9. Send to Shopify as a draft, check it, request publish, approve, run | **Store (Shopify)**, **Approval queue** | Shopify app | free |

Or run every safe step in one go with `python run.py all` (it never publishes or sends anything).

---

## UK checklist before you sell (read this)

The system flags these, but they're your responsibility. It's not legal or tax advice.

- **Product safety:** electricals, toys, cosmetics, batteries and food-contact items have legal requirements
  (UKCA/CE marking, a UK responsible person for cosmetics, test reports). As the UK seller you can be liable.
  Get the supplier's documents **before** listing, and order a sample.
- **VAT:** you must register once your 12-month turnover passes **£90,000**. **Goods shipped from outside the
  UK** have special rules for consignments of **£135 or less** (VAT can be due at the point of sale). Speak to an
  accountant before selling items that ship from abroad.
- **Consumer rights:** customers can cancel online orders within **14 days** of delivery, and faulty goods can be
  rejected within 30 days. You need a practical **UK return address** (overseas returns are slow and costly).
- **Advertising:** no fake reviews, fake urgency, unprovable "best" claims, or health claims (the claim checker
  helps, but read everything).
- **Data protection:** publish a privacy policy and pay the ICO data protection fee if it applies to you
  ([ico.org.uk/fee](https://ico.org.uk/fee)). Templates: `python run.py policies` (see below).
- **Business details:** show your business name, a geographic address and an email on your site.

---

## How each part works

### Finding products

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

### Suppliers and profit

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

### Competitor research

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

### Product pages and ad scripts

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

### Videos

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

### Publishing to Shopify

Dashboard → **Store (Shopify)**. For each product with approved content:

1. **Send to Shopify as draft**: creates the product on Shopify as a **DRAFT** (customers can't see it) with
   your approved title, description, FAQs, SEO text, tags, price and licensed photos. Sending again
   **updates the same product**, never a duplicate. Nothing is sent if nothing changed.
2. Check the draft in your Shopify admin (the page links to it). Set up shipping rates and your
   policies in Shopify if you haven't already.
3. **Request publish** → it appears in the **Approval queue** → **Approve** → **Run**. Only then is the
   product set to Active and published to your Online Store.
4. **Unpublish** takes it off sale again (back to draft) at any time.

Safety checks before anything is sent: an approved product page, a price, a chosen supplier, no
high-risk claims, and, for products with compliance flags (electrical, toys, cosmetics...), your
confirmation that you've checked the rules and have the supplier's certificates. Prices are never shown
with a fake "was" price. In **dry-run mode** every step only logs what it would do.

#### Connect Shopify (about 15 minutes)

Shopify no longer lets you create the old "custom app" tokens in the store admin, so you create a
small private app in Shopify's **Dev Dashboard** instead:

1. Go to **[dev.shopify.com/dashboard](https://dev.shopify.com/dashboard/)** and log in with your Shopify account.
2. For testing, create a free **development store** from the **Dev stores** page there. (Using the Dev Dashboard
   keeps the app and store in the same organisation, which this connection method needs.)
3. **Apps → Create app → Start from Dev Dashboard**, name it e.g. "Dropship Agent", **Create**.
4. On **Versions**, create a version: leave the App URL as the default
   (`https://shopify.dev/apps/default-app-home`), and under **Scopes** add:
   `read_products`, `write_products`, `read_publications`, `write_publications`. Click **Release**.
5. **Install app** → choose your store → **Install**.
6. Open the app's **Settings** and copy the **Client ID** and **Client secret** into `.env`:
   ```
   SHOPIFY_CLIENT_ID=...
   SHOPIFY_CLIENT_SECRET=...
   ```
7. In `config.yaml`, set `store.shop` to your store's subdomain (`my-store` for `my-store.myshopify.com`).
8. Test it (read-only): `python run.py publish --test-connection`, or **Test connection** on the Store page.

If you see `shop_not_permitted`, the app and store are in different organisations: create the store from the
Dev Dashboard's **Dev stores** page. If you already have an older admin-created custom app token, you can put
it in `SHOPIFY_ACCESS_TOKEN` instead of steps 3-6.

To publish for real you also need `dry_run: false` in `config.yaml`. Approvals are still required.


### Scheduled research and notifications

Set `schedule.enabled: true` in `config.yaml` and research runs automatically (default: daily at 07:40 UK
time) while the dashboard is open, or in its own window with `python run.py scheduler`. If your computer
was off, a missed run happens once when it's back on. Only free, read-only research is ever scheduled.

New products scoring 70+ (`schedule.notify_min_score`), budget warnings and failed runs appear under
**Notifications** (with a badge). Optionally they can also be emailed **to you**
(`notifications` in `config.yaml`, password in `.env` as `SMTP_PASSWORD`; Gmail needs an *app password*).

### Policy templates

`python run.py policies` fills UK templates for your **privacy policy**, **returns and refunds** and **delivery**
with your details from `brand.yaml` (`business:` section) into `output/policies/`. Search for `[CHECK` and fix every
item, then paste them into Shopify → Settings → Policies. They're starting points, not legal advice.

---

## Commands

| Command | What it does |
|---|---|
| `python run.py doctor` | Setup checklist: what's ready, what's missing, how to fix it |
| `python run.py init` | Create the database and folders |
| `python run.py dashboard` | Open the dashboard at http://127.0.0.1:8000 |
| `python run.py status` | Mode, budget used, pending approvals |
| `python run.py all` | Run every safe step in order and list what's waiting for you (`--with-content` also writes pages/ads: paid). Never publishes or sends |
| `python run.py research` | Score and rank your product ideas (`--explain 3`: Claude summaries for the top 3, paid) |
| `python run.py suppliers` | Score suppliers and show true profit with each (`--draft-emails`: drafts only, never sends) |
| `python run.py profit --price 19.99 --cost 4 --shipping 3` | Quick profit check |
| `python run.py competitor https://store.co.uk` | Research a competitor: facts only, checks robots.txt and terms first |
| `python run.py content` | Write a page + 3 ad scripts for products with a chosen supplier (`--product`, `--what page/ads/all`; paid) |
| `python run.py video` | Render approved ad scripts into MP4s (`--product`, `--music track.mp3`, `--voice recorded`) |
| `python run.py publish` | Shopify readiness (`--send-drafts`; `--test-connection`, read-only) |
| `python run.py scheduler` | Run scheduled research in this window |
| `python run.py policies` | Fill the UK policy templates |
| `python run.py demo` | Add a harmless demo approval request |
| `python run.py --dry-run <command>` | Force dry-run for one command, whatever config says |

## Settings you might change

**`config.yaml`** (safe to share; no secrets):
- `dry_run`: `true` (safe, default) or `false` (approved actions really happen).
- `budget`: monthly cap for paid AI calls (£25), warning level (80%), per-call approval threshold (£1).
- `llm`: which Claude model to use, per task if you like (`task_models`), and price assumptions.
- `business`: VAT registration and exchange rates. `profit`: fees and assumptions. **Check against your Shopify plan.**
- `research`, `suppliers`: scoring weights and thresholds; `avoid_categories`.
- `competitors`: how politely sites are fetched. `video`: size, captions, colours, ffmpeg location.
- `store`: your Shopify subdomain. `schedule`, `notifications`: see above.

**`brand.yaml`**: store name, tone, what you really offer (returns window, free delivery, guarantee), business details.

**`.env`** (secret, never share): API keys and passwords. `.env.example` lists them all.

## API keys and accounts

| What | Needed for | Where | Cost |
|---|---|---|---|
| Anthropic API key | Product pages, ad scripts, AI summaries | [console.anthropic.com](https://console.anthropic.com) → API Keys. Set a spend limit under Billing too | pay-as-you-go, capped by your budget |
| Shopify Dev Dashboard app | Publishing | See *Connect Shopify* above | free (your Shopify plan is separate) |
| ffmpeg | Videos | Windows: `winget install Gyan.FFmpeg` · Mac: `brew install ffmpeg` | free |
| Meta Ad Library token | Optional ad-saturation data | [research/README.md](research/README.md) | free |
| Email (SMTP) | Optional email notifications | your email provider's SMTP settings | free |

Paid data tools (SerpApi, Keepa, Minea...), AI voices and supplier APIs (e.g. CJ Dropshipping) can be plugged in
later. Each needs an account first, so it can be tested against the real service.

## How the safety pieces work

- **Approval queue:** real actions (like publishing) are requests. You approve, then click **Run**. Only approved
  requests run, and only once. Content and videos have their own approve/reject buttons.
- **Dry-run:** until you set `dry_run: false`, approved actions are only logged ("would have done X").
- **Budget guard:** every paid call's worst-case cost is checked against your monthly cap first. Warning at 80%.
- **Claim checker:** risky claims in pages and ads are highlighted; high-risk ones block approval.
- **No made-up data:** missing data is shown as missing. Every number records its source and date.
- **Licences:** footage and music are only used once you've recorded their licence.
- **Polite, permitted research:** robots.txt and terms are checked first; we stop if automation isn't allowed.
- **Local only:** the dashboard only listens on your own computer (127.0.0.1), and its buttons can't be
  triggered by other websites.
- **Everything logged:** Logs & costs shows what was done, from which source, at what cost.

## Troubleshooting

- **`python` not found:** reinstall Python and tick *Add to PATH* (Windows), or use `python3` (Mac).
- **`No module named ...`:** activate the environment (`.venv\Scripts\activate`) and run `pip install -r requirements.txt`.
- **Something's not working:** run `python run.py doctor`, and check **Logs & costs** for the error message.
- **Changed config.yaml or .env:** restart the dashboard (Ctrl+C, then `python run.py dashboard`).
- **Shopify `shop_not_permitted`:** see *Connect Shopify* above.
- **A button shows an error pop-up:** the message says what's wrong; it's also on the Logs page.

## Running the tests
```bash
python -m pytest -q
```
Over 200 tests with a temporary database and fake API responses: they cost nothing and contact no real services.
(Two tests render real videos if ffmpeg is installed.)

## Project layout
```
dropship-agent/
  app/core/          config, database, approvals, budget, logging, Claude wrapper, scheduler, notifications
  app/modules/       product_research, supplier_finder, profit_calculator, competitor_analyzer,
                     content_generator, video_maker, store_publisher
  app/api/           dashboard pages
  app/dashboard/     HTML templates, styles, HTMX
  research/          your product ideas, Google Trends downloads, suppliers (examples included)
  media/             your footage, music and voiceovers (not uploaded to GitHub)
  policies/          UK policy templates
  output/            videos and filled policies (not uploaded)
  tests/             automated tests
  config.yaml        settings · brand.yaml: brand voice and business details · .env.example: secrets template
  run.py             the command line
```
Your database, logs and caches live in `data/` and `logs/` on your computer and are never uploaded.
