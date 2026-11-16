# How to feed product research

Research uses **your** product ideas plus data you export from allowed sources.
Nothing is scraped, and nothing is guessed: blank = unknown.

## 1. List your ideas: `candidates.csv`

Copy `candidates.example.csv` to `candidates.csv` (Excel or Google Sheets work; save as CSV)
and add one row per product idea:

| Column | Meaning | Where to find it |
|---|---|---|
| `name` | Product name | You |
| `category` | e.g. pet, home, outdoor | You |
| `keywords` | Search terms, separated by `;`. The first one is used for Google Trends and Meta | You |
| `est_unit_cost_gbp` | Supplier price per unit | Supplier listing (CJ, AliExpress, ...) |
| `est_shipping_gbp` | Shipping per unit to the UK | Supplier listing |
| `est_sell_price_gbp` | Your planned price **including VAT** | Look at what others charge |
| `competitor_stores` | How many stores you found selling it | Google search / Google Shopping |
| `ads_seen` | How many different advertisers run ads for it | facebook.com/ads/library (free, search the keyword) |
| `source_note` | Where/when you got the numbers | e.g. "CJ listing 16/11/2026" |
| `notes` | Anything else | You |

Rows whose name starts with `EXAMPLE` are skipped.

## 2. Add demand data: Google Trends exports (free)

1. Go to [trends.google.com](https://trends.google.com), set the country to **United Kingdom** and the time range to **Past 12 months** (or longer for seasonality).
2. Type a product keyword. You can compare up to 5 at once.
3. On the **Interest over time** chart click the **download (↓) icon**. You get `multiTimeline.csv`.
4. Rename it (e.g. `pets-2026-11.csv`) and put it in `research/trends/`.

The keyword in Google Trends must match one of the product's `keywords`.
Trends numbers are relative (0-100) **within one export**, so compare similar products in the same export.

## 3. Run it

```bash
python run.py research              # free
python run.py research --explain 3  # + Claude summaries for the top 3 (paid, a few pence)
```

Then open the dashboard → **Opportunities**.

## Optional: Meta Ad Library API (ad saturation, free but needs setup)

Meta's official API gives advertiser counts automatically instead of you counting `ads_seen`.
Limit: for normal (non-political) ads it only covers ads shown in the **EU**, so we use
Ireland/France/Germany as a stand-in for the UK.

1. Confirm your identity at facebook.com/ID (Meta requires this for Ad Library API access; it can take a few days).
2. Create an app at developers.facebook.com → **My Apps → Create App**.
3. Get a user access token from the **Graph API Explorer** for that app.
4. Paste it in `.env` as `META_AD_LIBRARY_TOKEN=...` and set `meta_enabled: true` in `config.yaml`.

Tokens from the Graph API Explorer expire after about an hour unless exchanged for a long-lived one; I'll help with that when you get there.
