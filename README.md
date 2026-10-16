# GFS Logistics: AR Aging Dashboard

**Live site:** https://nelsonnicholson.github.io/gfs-ar-aging/ (passphrase protected)

The dashboard has three views: **Entire company**, **Division G1** and **Division G2**. Click any customer to open a panel showing their open invoices and the date and amount of their last payment.

**Customer statements:** in a customer's panel, **Download PDF** builds that customer's statement in the browser. G1 customers get the GFS Logistics template and G2 customers get the Texas Cartage Warehouse, LLC template, each with its own logo, address and bank remittance details. Statements list every open item and age it by due date as of the data date. Bill-to addresses come from the customer's default mailing address in Sage. The bank details are stored only inside the encrypted data, never in the public code.

## How it works

```
Sage Intacct ──► GitHub Action (weekdays ~6:05 PM CT) ──────────► encrypted data.enc.json ──► GitHub Pages
                 fetch_ar.py                                                                 index.html decrypts
                                                                                             with your passphrase
```

- **Live mode** (once the `SAGE_*` secrets exist): each run pulls open invoices, credit memos, unapplied payments and advances, plus each customer's last cash receipt, from the Sage REST API.
- **Snapshot mode** (until then): republishes the latest verified Sage snapshot. That snapshot is stored encrypted in `seed.enc.json` and unlocked by the `SEED_KEY` secret. To refresh it, ask Claude to pull a new snapshot from Sage, then upload the new `seed.enc.json` here. The site updates within about a minute.
- **On-demand refresh:** Actions → Refresh AR aging dashboard → **Run workflow**.
- **Every run checks the total against Sage.** The company total must tie to the sum of Sage customer balances; if it doesn't, the dashboard shows a red banner.

## Secrets (Settings → Secrets and variables → Actions)

| Secret | Needed for | Notes |
|---|---|---|
| `DASHBOARD_PASSWORD` | always | Passphrase viewers type. Change it to revoke access, then re-run the workflow. |
| `SEED_KEY` | always | Unlocks `seed.enc.json`, which holds the Oct 16 snapshot and the customer→division list. |
| `SAGE_CLIENT_ID`, `SAGE_CLIENT_SECRET`, `SAGE_USERNAME` | live data | `SAGE_USERNAME` = `webservicesuser@CompanyID` |
| `CUSTOMER_DIVISIONS` | optional | Adds or changes divisions without a code change. One customer per line: `CUSTOMERID,G1`. You can paste two columns straight from Excel. |

## Divisions

The G1/G2 assignments come from the customer list provided on Oct 16, 2026 (138 customers). A customer that isn't on the list shows as **Unassigned**: a banner names them and the run log warns. To fix one, add a line to the `CUSTOMER_DIVISIONS` secret, e.g. `NEWCUST01,G2`, and re-run the workflow.

## Security

- Sage credentials live only in GitHub secrets. The AR data is never stored unencrypted in the repo; only encrypted files are committed or published.
- The site is public, but the data file is AES-256 encrypted. Use a long passphrase.

## Files

| File | Purpose |
|---|---|
| `index.html` | Dashboard (no external libraries) |
| `logo.png` | GFS Logistics logo (G1 statements and dashboard) |
| `tcw-logo.png` | Texas Cartage Warehouse logo (G2 statements) |
| `jspdf.umd.min.js` | PDF library used to build statements (jsPDF 2.5.1, MIT license) |
| `fetch_ar.py` | Pulls from Sage, or unlocks the snapshot; assigns divisions; encrypts for viewers |
| `seed.enc.json` | Encrypted Oct 16 snapshot and division list |
| `.github/workflows/refresh.yml` | Schedule and deployment |
| `requirements.txt` | Python packages |
