#!/usr/bin/env python3
"""
Pull open AR from Sage Intacct (REST API), build an aging snapshot, encrypt it,
and write it where the static dashboard can load it.

Open AR = AR invoices/credit memos with a balance
        - unapplied overpayments (posted-over-payment)
        - unapplied advances
This ties to the sum of customer balances in Sage (checked on every run).

Environment variables (set as GitHub Actions secrets):
  SAGE_CLIENT_ID       OAuth client ID from the Sage developer portal
  SAGE_CLIENT_SECRET   OAuth client secret
  SAGE_USERNAME        Web Services user, e.g.  ws_dashboard@GFSLogistics
                       (multi-entity: ws_dashboard@GFSLogistics|ENTITYID)
  DASHBOARD_PASSWORD   Passphrase viewers type to unlock the dashboard
Optional:
  SAGE_API_BASE        default https://api.intacct.com/ia/api/v1
  COMPANY_NAME         default "GFS Logistics"
  REPORT_TZ            default America/Chicago

Usage:
  python scripts/fetch_ar.py --out site/data.enc.json
  python scripts/fetch_ar.py --sample --out site/data.enc.json   # fake data, no Sage call
  python scripts/fetch_ar.py --plain-out snapshot.json ...        # also write unencrypted JSON (local debugging only)
"""
import argparse
import base64
import datetime as dt
import json
import os
import random
import sys
from decimal import Decimal
from zoneinfo import ZoneInfo

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 310_000
PAGE_SIZE = 1000


# --------------------------------------------------------------------------- Sage REST
class Sage:
    def __init__(self):
        self.base = os.environ.get("SAGE_API_BASE", "https://api.intacct.com/ia/api/v1").rstrip("/")
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.token = self._get_token()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _get_token(self):
        payload = {
            "grant_type": "client_credentials",
            "client_id": _env("SAGE_CLIENT_ID"),
            "client_secret": _env("SAGE_CLIENT_SECRET"),
            "username": _env("SAGE_USERNAME"),
        }
        url = f"{self.base}/oauth2/token"
        r = self.session.post(url, json=payload, timeout=60)
        if r.status_code >= 400:  # some tenants expect form encoding
            r = self.session.post(url, data=payload, timeout=60)
        if r.status_code >= 400:
            sys.exit(f"Sage authentication failed ({r.status_code}): {r.text[:500]}")
        return r.json()["access_token"]

    def query(self, obj, fields, filters=None, order_by=None):
        rows, start = [], 1
        while True:
            body = {"object": obj, "fields": fields, "start": start, "size": PAGE_SIZE}
            if filters:
                body["filters"] = filters
            if order_by:
                body["orderBy"] = order_by
            r = self.session.post(f"{self.base}/services/core/query", json=body, timeout=120)
            if r.status_code >= 400:
                sys.exit(f"Sage query on {obj} failed ({r.status_code}): {r.text[:500]}")
            data = r.json()
            rows.extend(data.get("ia::result", []))
            nxt = (data.get("ia::meta") or {}).get("next")
            if not nxt:
                return rows
            start = int(nxt)


def _env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"Missing required environment variable {name}")
    return v


def g(rec, path, default=None):
    """Read 'customer.id' whether the API returned flat dotted keys or nested objects."""
    if path in rec:
        return rec[path]
    cur = rec
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def money(v):
    return float(Decimal(str(v or "0")).quantize(Decimal("0.01")))


# --------------------------------------------------------------------------- Snapshot
def build_from_sage():
    s = Sage()
    items = []

    inv = s.query(
        "accounts-receivable/invoice",
        ["invoiceNumber", "customer.id", "customer.name", "invoiceDate", "dueDate",
         "totalBaseAmount", "totalBaseAmountDue", "referenceNumber"],
        filters=[{"$ne": {"totalBaseAmountDue": "0"}}],
    )
    for r in inv:
        bal = money(g(r, "totalBaseAmountDue"))
        items.append({
            "t": "cm" if bal < 0 else "inv",
            "n": g(r, "invoiceNumber") or "",
            "ref": g(r, "referenceNumber") or "",
            "c": g(r, "customer.id"),
            "cn": g(r, "customer.name"),
            "d": g(r, "invoiceDate"),
            "due": g(r, "dueDate") or g(r, "invoiceDate"),
            "amt": money(g(r, "totalBaseAmount")),
            "bal": bal,
        })

    ops = s.query(
        "accounts-receivable/posted-over-payment",
        ["id", "documentNumber", "customer.id", "customer.name", "paymentDate",
         "baseCurrency.totalAmount", "baseCurrency.totalAmountDue"],
        filters=[{"$ne": {"baseCurrency.totalAmountDue": "0"}}],
    )
    for r in ops:
        d = g(r, "paymentDate")
        items.append({
            "t": "op", "n": g(r, "documentNumber") or f"Overpayment {g(r, 'id')}", "ref": "",
            "c": g(r, "customer.id"), "cn": g(r, "customer.name"), "d": d, "due": d,
            "amt": -abs(money(g(r, "baseCurrency.totalAmount"))),
            "bal": -abs(money(g(r, "baseCurrency.totalAmountDue"))),
        })

    adv = s.query(
        "accounts-receivable/advance",
        ["id", "advanceNumber", "referenceNumber", "customer.id", "customer.name",
         "paymentInformation.receiptDate", "totalEntered", "paymentInformation.totalDue"],
        filters=[{"$ne": {"paymentInformation.totalDue": "0"}}],
    )
    for r in adv:
        d = g(r, "paymentInformation.receiptDate")
        items.append({
            "t": "adv", "n": g(r, "advanceNumber") or f"Advance {g(r, 'id')}",
            "ref": g(r, "referenceNumber") or "",
            "c": g(r, "customer.id"), "cn": g(r, "customer.name"), "d": d, "due": d,
            "amt": -abs(money(g(r, "totalEntered"))),
            "bal": -abs(money(g(r, "paymentInformation.totalDue"))),
        })

    cust = s.query("accounts-receivable/customer", ["id", "totalDue"],
                   filters=[{"$ne": {"totalDue": "0"}}])
    check_total = round(sum(money(g(r, "totalDue")) for r in cust), 2)
    return items, check_total


def build_sample():
    """Synthetic data for testing the page without touching Sage."""
    rnd = random.Random(7)
    names = ["Acme Plastics", "Bluebonnet Foods", "Cedar Freight Co", "Delta Polymers", "Evergreen Supply",
             "Frontier Metals", "Granite Packaging", "Harbor Chemicals", "Ironwood Textiles", "Juniper Beverages",
             "Keystone Adhesives", "Lone Star Pallets", "Magnolia Paper", "Northwind Coatings", "Oakridge Pigments"]
    today = dt.date.today()
    items = []
    for i, name in enumerate(names):
        cid = f"CUST{i + 1:03d}"
        for k in range(rnd.randint(1, 9)):
            d = today - dt.timedelta(days=rnd.randint(0, 200))
            due = d + dt.timedelta(days=rnd.choice([15, 30, 45]))
            amt = round(rnd.uniform(150, 40000), 2)
            bal = amt if rnd.random() > 0.2 else round(amt * rnd.uniform(0.1, 0.9), 2)
            items.append({"t": "inv", "n": f"I{9000 + len(items)}", "ref": "", "c": cid, "cn": name,
                          "d": d.isoformat(), "due": due.isoformat(), "amt": amt, "bal": bal})
        if rnd.random() < 0.2:
            d = (today - dt.timedelta(days=rnd.randint(0, 40))).isoformat()
            v = -round(rnd.uniform(100, 2000), 2)
            items.append({"t": "op", "n": f"Overpayment {i}", "ref": "", "c": cid, "cn": name,
                          "d": d, "due": d, "amt": v, "bal": v})
    return items, round(sum(x["bal"] for x in items), 2)


def snapshot(items, check_total, sample=False):
    tz = ZoneInfo(os.environ.get("REPORT_TZ", "America/Chicago"))
    now = dt.datetime.now(tz)
    total = round(sum(x["bal"] for x in items), 2)
    return {
        "v": 1,
        "company": os.environ.get("COMPANY_NAME", "GFS Logistics"),
        "sample": sample,
        "asOf": now.date().isoformat(),
        "generatedAt": now.isoformat(timespec="seconds"),
        "tz": str(tz),
        "total": total,
        "check": {"customerBalances": check_total, "ties": abs(total - check_total) < 0.005},
        "items": items,
    }


# --------------------------------------------------------------------------- Encryption
def encrypt(obj, password):
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=PBKDF2_ITERATIONS).derive(password.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, separators=(",", ":")).encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": PBKDF2_ITERATIONS,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/data.enc.json")
    ap.add_argument("--sample", action="store_true", help="use synthetic data instead of Sage")
    ap.add_argument("--plain-out", help="also write the unencrypted snapshot (never commit this)")
    args = ap.parse_args()

    items, check_total = build_sample() if args.sample else build_from_sage()
    snap = snapshot(items, check_total, sample=args.sample)

    password = _env("DASHBOARD_PASSWORD")
    if len(password) < 12:
        sys.exit("DASHBOARD_PASSWORD must be at least 12 characters (a 4-5 word passphrase is ideal).")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(encrypt(snap, password), f)
    if args.plain_out:
        with open(args.plain_out, "w") as f:
            json.dump(snap, f, indent=1)

    status = "ties" if snap["check"]["ties"] else "DOES NOT TIE"
    print(f"Wrote {args.out}: {len(items)} open items, net AR {snap['total']:,.2f} "
          f"({status} to customer balances {check_total:,.2f})")


if __name__ == "__main__":
    main()
