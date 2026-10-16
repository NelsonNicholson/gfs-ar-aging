# GFS Logistics — AR Aging Dashboard

A passphrase-protected AR aging dashboard hosted on GitHub Pages. A GitHub Action pulls open AR from Sage Intacct every hour during business hours, encrypts it, and redeploys the site.

```
Sage Intacct REST API ──► GitHub Action (hourly, M–F 7a–6p CT)
                             │  scripts/fetch_ar.py
                             │  • open invoices & credit memos
                             │  • unapplied overpayments & advances
                             │  • ties total to Sage customer balances
                             │  • AES-256 encrypts with your passphrase
                             ▼
                        GitHub Pages: index.html + data.enc.json
                             ▼
                        Browser: enter passphrase → decrypts & renders
```

**What's on the dashboard:** total open AR, current vs. past due, over-90, unapplied credits. It also shows an aging bar with clickable buckets and a sortable, searchable customer table that drills down to the invoices behind each row. You can export the view to CSV and switch between aging by due date and by invoice date.

---

## Security model — read this first

- **No Sage credentials ever reach the browser.** They live only in GitHub Actions secrets.
- **The AR data is never committed to the repo.** Each run deploys it straight to Pages.
- **A GitHub Pages site is publicly reachable**, unless you're on GitHub Enterprise Cloud with private Pages. Anyone with the URL can download `data.enc.json`, but it's AES-256-GCM encrypted with a key derived from `DASHBOARD_PASSWORD` (PBKDF2, 310k iterations). **The protection is only as strong as the passphrase.** Use 5 or more random words, not a company name plus a year.
- **Everyone shares one passphrase.** There's no per-user login or audit trail. To revoke access, change the `DASHBOARD_PASSWORD` secret and re-run the workflow. The old passphrase stops working immediately.
- If you need per-person sign-in later, the same script and page can be moved to Azure Static Web Apps with Microsoft 365 login. The only change is the deploy step.

---

## One-time setup

### 1. Sage Intacct: API access (company admin, ~20 min)

1. **Turn on Web Services:** Company → Admin → Subscriptions → **Web Services** → On.
2. **Register an app:** Sign in to the [Sage developer portal](https://developer.sage.com/intacct/) with your Web Services developer account and create an application. Note the **Client ID** and **Client Secret**. You need an active Web Services developer license (sender ID); ask your Sage partner if you don't have one.
3. **Create a dedicated Web Services user:** Company → Admin → Web Services users → Add, e.g. `ws_ar_dashboard`. Give it a role with **read-only** access to Accounts Receivable: customers, invoices, adjustments, advances and payments.
4. **Authorize the app:** Company → Setup → Company → Edit → Security → **Authorized client applications** → Add. Enter the Client ID and the Web Services user ID (case-sensitive).
5. Find your **Company ID** at Company → Setup → Company.

### 2. GitHub repo (~10 min)

1. Create a new repository, e.g. `gfs-ar-aging`, and upload everything in this folder, including the hidden `.github` folder.
   - A **private repo** needs a paid GitHub plan (Pro, Team or Enterprise) to use Pages. On the free plan the repo must be public. That's fine: the code holds no secrets and the data is never committed.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Settings → Secrets and variables → Actions → New repository secret.** Add four secrets:

   | Secret | Value |
   |---|---|
   | `SAGE_CLIENT_ID` | from step 1.2 |
   | `SAGE_CLIENT_SECRET` | from step 1.2 |
   | `SAGE_USERNAME` | `ws_ar_dashboard@YourCompanyID` (multi-entity: `ws_ar_dashboard@YourCompanyID\|ENTITYID`) |
   | `DASHBOARD_PASSWORD` | a long passphrase, e.g. `copper-meadow-lantern-orbit-tulsa` |

4. **Actions tab → Refresh AR aging dashboard → Run workflow.** When it turns green, the site URL appears on the run (usually `https://<org>.github.io/gfs-ar-aging/`).

> Until the three `SAGE_*` secrets are added, the workflow deploys **sample data** (with a yellow banner) so you can see the site working. Only `DASHBOARD_PASSWORD` is needed for that.

### 3. Check the first run

The run log prints a line like this:

```
Wrote site/data.enc.json: 275 open items, net AR 1,351,709.18 (ties to customer balances 1,351,709.18)
```

For reference, a check against Sage on **Oct 16, 2026** gave these figures:

| Due-date bucket | Invoices | Open amount |
|---|---:|---:|
| Current | 71 | $652,008.05 |
| 1–30 past due | 99 | $331,988.38 |
| 31–60 past due | 24 | $74,081.32 |
| 61–90 past due | 19 | $47,300.18 |
| Over 90 past due | 58 | $249,371.10 |
| **Invoices & credit memos** | **271** | **$1,354,749.03** |
| Unapplied overpayments (2) | | –$2,374.70 |
| Unapplied advances (2) | | –$665.15 |
| **Net open AR = Sage customer balances** | | **$1,351,709.18** |

Compare one bucket to Sage's own AR Aging report for the same date. If the buckets differ, it's usually a difference in aging-date settings, not missing data.

---

## Day to day

- **Refresh schedule:** hourly at :05, Mon–Fri, roughly 7 AM–6 PM Central. You can change the `cron` line in `.github/workflows/refresh.yml`; times are UTC. For an on-demand refresh, use Actions → Run workflow.
- **Stale-data warning:** the page shows a red banner if the data is more than 26 hours old, which means the job is failing. Check the Actions tab. GitHub also emails the repo owner when a scheduled run fails.
- **Heads-up:** GitHub pauses scheduled workflows in repos with no commits for 60 days. If you get the email, click "Enable workflow" in the Actions tab.
- **Rotate the passphrase:** update the `DASHBOARD_PASSWORD` secret, then click Run workflow.

## Local testing (optional)

```bash
pip install -r requirements.txt
DASHBOARD_PASSWORD="any long test phrase" python scripts/fetch_ar.py --sample --out site/data.enc.json
python -m http.server -d site 8000      # open http://localhost:8000
```

`--sample` uses made-up data and never contacts Sage.

## Files

| Path | Purpose |
|---|---|
| `scripts/fetch_ar.py` | Signs in to Sage (OAuth client credentials), queries open AR, checks the tie-out, encrypts |
| `site/index.html` | The dashboard; decrypts in the browser, no external libraries |
| `.github/workflows/refresh.yml` | Schedule plus Pages deployment |
| `.gitignore` | Keeps generated data out of git |
