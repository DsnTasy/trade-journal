# Start on Windows and go live: step by step

Work through the parts in order. Everything up to Part 4 is free and safe: the system starts in
**dry-run mode**, so nothing is published, sent or paid for.

---

## Part 1: Install (about 20 minutes, once)

**1. Install Python 3.13**
1. Go to **python.org/downloads/windows**, find the latest **Python 3.13.x** release and download the
   *Windows installer (64-bit)*. (3.13 is the version this project is tested with; newer versions may
   not work with every package yet.)
2. Run it. On the first screen **tick "Add python.exe to PATH"**, then click **Install Now**.

**2. Install ffmpeg (for videos)**
1. Press the **Windows key**, type `cmd`, press Enter (a black Command Prompt window opens).
2. Type `winget install Gyan.FFmpeg` and press Enter. Accept if asked. Close the window when done.

**3. Download the project**
1. Log in to GitHub in your browser, then open:
   `https://github.com/DsnTasy/trade-journal/archive/refs/heads/claude/new-session-baq6ot.zip`
2. Open your **Downloads** folder → right-click the ZIP → **Extract All** → choose `Documents` → **Extract**.
3. Open the extracted folder, then the **`dropship-agent`** folder inside it.

**4. Set it up**
1. Double-click **`setup.bat`**. It installs everything (a few minutes) and finishes with a checklist.
   - If Windows shows "Windows protected your PC", click **More info → Run anyway** (it's the file you just downloaded).
   - "Python was not found"? Reinstall Python and tick *Add python.exe to PATH*.
2. Double-click **`start.bat`**. Your browser opens the dashboard at **http://127.0.0.1:8000**.
   **Keep the black window open** while you use it; close it to stop.

From now on, just double-click **`start.bat`** each time.

**5. Your details**
Right-click **`brand.yaml`** → **Open with → Notepad**. Put in your store name (and, later, the `business:`
details for your policies). Save.

---

## Part 2: Find products (free)

1. Open `research\candidates.csv` in **Excel**. Add one row per product idea (see `research\README.md` for each
   column). Delete the EXAMPLE rows, or leave them; they're ignored. **Save as CSV**.
2. Go to **trends.google.com**, set **United Kingdom** and **Past 12 months**, search a product keyword, click the
   **download arrow** on "Interest over time", and move the file into `research\trends\`.
3. In the dashboard: **Notifications → Run research now**. Then **Opportunities** shows the ranked list.
   Click a product for its details, then **Shortlist** the good ones.
4. Optional: **Competitor reports**: paste a competitor's store address.

Want ideas? Ask Claude for a list of product ideas in your niche, then score them here.

---

## Part 3: Suppliers, samples, content and videos

1. **Suppliers:** open `research\suppliers.csv` in Excel and add 2-5 suppliers for each shortlisted product
   (copy the numbers from CJ Dropshipping, AliExpress, Spocket, etc.). Save, then in Command Prompt in this
   folder run `.venv\Scripts\activate` and `python run.py suppliers`, or use **Suppliers** in the dashboard.
2. **Profit:** check **Profit calculator**. Change the ad cost per sale and the Shopify fees to your real numbers.
   Only continue with products marked **go**.
3. **Choose a supplier**, then on the supplier's page **Draft an enquiry email**, copy it, send it yourself,
   and **order a sample**.
4. **Content:** get an Anthropic API key (console.anthropic.com → Billing: add a card and set a monthly limit →
   API Keys → Create). Open `.env` in Notepad and paste it after `ANTHROPIC_API_KEY=`. Restart `start.bat`.
   Then **Content → Write product page / Write 3 ad scripts** (about 12p), fix anything highlighted, **Approve**.
5. **Videos:** film the sample on your phone: 5-10 short clips (close-ups, in use, before/after). Copy them to
   `media\<product-folder>\` (the Videos page shows the folder names). On **Videos**, record each file's licence,
   then **Render** and **Approve**.

### AI clips and voices (optional)

- **AI video clips** (Runway, Google Veo, Kling, Sora...): make *background and lifestyle* shots on the tool's
  website, such as a living room or a dog on a sofa, and save them into the same product folder. Record their licence as
  **"Made with an AI tool"** and note which tool. Keep real footage of your sample for the product itself.
- Videos containing AI clips are tagged **"contains AI media"**: tick the AI-generated label when you post
  on TikTok, Instagram or Facebook.
- **Voiceover:** record yourself on your phone reading the script and save it as `voice-demo.mp3` (or
  `voice-problem_solution.mp3`, `voice-social_proof.mp3`) in the product folder, then choose **My recording** when
  rendering. An AI voice (e.g. ElevenLabs) can be connected once you have an account. Ask Claude to add it.

---

## Part 4: Go live

### Business basics (before your first sale)
- **HMRC:** register for Self Assessment as a sole trader (gov.uk → "Register for Self Assessment") once your
  trading income is likely to go over £1,000 a year.
- **ICO:** check if you need to pay the data protection fee (ico.org.uk/fee). Most small online shops do.
- **VAT:** register when turnover passes £90,000. If products ship from **outside the UK**, speak to an accountant
  first: special VAT rules apply to imported goods of £135 or less.
- **Returns address:** have a practical UK address for returns.

### Shopify store
1. **shopify.com** → start a free trial → then choose a paid plan (the store is password-protected until you do).
2. **Settings → Payments:** turn on Shopify Payments.
3. **Settings → Shipping and delivery:** set your UK delivery rates.
4. **Policies:** in Command Prompt (in this folder) run `.venv\Scripts\activate` then `python run.py policies`.
   Open the files in `output\policies\`, fix every `[CHECK: ...]`, and paste them into **Settings → Policies**.
5. Add your business name, address and email to the footer (Online Store → Themes → Customize).
6. Place a **test order** (Settings → Payments → test mode), then turn test mode off.

### Connect the system to Shopify
1. Go to **dev.shopify.com/dashboard**. First create a **development store** (Dev stores page) to test with.
2. **Apps → Create app → Start from Dev Dashboard**, name it "Dropship Agent".
3. **Versions:** keep the default App URL; add scopes `read_products`, `write_products`, `read_publications`,
   `write_publications`; click **Release**.
4. **Install app** on your store.
5. App **Settings** → copy **Client ID** and **Client secret** into `.env`:
   `SHOPIFY_CLIENT_ID=...` and `SHOPIFY_CLIENT_SECRET=...`
6. In `config.yaml`, set `shop: "your-store"` (the part before `.myshopify.com`). Restart `start.bat`.
7. **Store (Shopify) → Test connection.**

> For your **real** store, the app and store must be in the same Shopify organisation. If you see
> `shop_not_permitted`, ask Claude to look up the current steps to add your store to your organisation.

### Switch on and publish
1. In `config.yaml`, change `dry_run: true` to `dry_run: false`. Save and restart `start.bat`.
2. **Store (Shopify) → Send to Shopify as draft** → check it in your Shopify admin.
3. **Request publish → Approval queue → Approve → Run.** The product is live.
4. Remove the store password (Online Store → Preferences) when you're ready for customers.

---

## Part 5: Meta (Facebook and Instagram) ads (optional)

1. Create a **Facebook Page** and link an **Instagram business account**.
2. **business.facebook.com** → create a Business account → **Ad account**: currency **GBP**, time zone **London**
   (can't be changed later). Add a payment method and an **account spending limit**.
3. In Shopify, install the **Facebook & Instagram** app (connects products and purchase tracking).
4. In **Ads Manager**, create a **Sales** campaign with one of your approved videos and script.
   Start with **£10-20 a day** for one product for 3-5 days.
5. Compare **cost per purchase** with the **break-even ad cost per sale** in the profit calculator. Above it means
   you're losing money: stop or change the ad. Put your real cost per sale into the profit calculator.
6. Label AI-generated content when asked, and never use fake reviews or urgency (Meta rejects them too).

---

## If something goes wrong
- Run `python run.py doctor` (after `.venv\Scripts\activate`) or open **Setup checklist** in the dashboard.
- Error messages also appear on **Logs & costs**.
- Changed `config.yaml` or `.env`? Close the black window and double-click `start.bat` again.
- Still stuck: copy the error message and ask Claude.
