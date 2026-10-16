# GFS Logistics: AR Aging Dashboard

**Live site:** https://nelsonnicholson.github.io/gfs-ar-aging/ (passphrase protected)

The dashboard has three views: **Entire company**, **Division G1** and **Division G2**. Click any customer to open a panel showing their open invoices and the date and amount of their last payment.

## How it works

```
Sage Intacct ──► GitHub Action (hourly, Mon–Fri 7 AM–6 PM CT) ──► encrypted data.enc.json ──► GitHub Pages
                 fetch_ar.py                                                                 index.html decrypts
                                                                                             with your passphrase
```

- **Live mode** (once the `SAGE_*` secrets exist): each run pulls open invoices, credit memos, unapplied payments and advances, plus each customer's last cash receipt, from the Sage REST API.
- **Snapshot mode** (until then): republishes the verified Sage snapshot from Oct 16, 2026. That snapshot is stored encrypted in `seed.enc.json` and unlocked by the `SEED_KEY` secret.
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
| `logo.png` | GFS Logistics logo |
| `fetch_ar.py` | Pulls from Sage, or unlocks the snapshot; assigns divisions; encrypts for viewers |
| `seed.enc.json` | Encrypted Oct 16 snapshot and division list |
| `.github/workflows/refresh.yml` | Schedule and deployment |
| `requirements.txt` | Python packages |
