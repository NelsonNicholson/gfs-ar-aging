#!/usr/bin/env python3
"""
Build the encrypted AR aging snapshot for the GFS Logistics dashboard.

Modes (picked automatically):
  live      SAGE_CLIENT_ID / SAGE_CLIENT_SECRET / SAGE_USERNAME are set
            -> pulls open AR + last payments from the Sage Intacct REST API
  snapshot  no Sage credentials, but SEED_KEY is set
            -> republishes the Oct 16, 2026 snapshot stored in seed.enc.json
  sample    --sample flag -> made-up data for local testing

Division mapping (G1 / G2) comes from the encrypted seed bundle. To change it
without a code change, set the optional secret CUSTOMER_DIVISIONS to lines of
"CUSTOMERID,G1" (comma or tab separated); those lines override the bundle.

Required secrets: DASHBOARD_PASSWORD, SEED_KEY. Optional: SAGE_*, CUSTOMER_DIVISIONS.
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

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 310_000
PAGE_SIZE = 1000
SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.enc.json")
b64e = lambda b: base64.b64encode(b).decode("ascii")
b64d = lambda s: base64.b64decode(s)


def env(name, required=True):
    v = (os.environ.get(name) or "").strip()
    if required and not v:
        sys.exit(f"Missing required secret / environment variable {name}")
    return v


def money(v):
    return float(Decimal(str(v or "0")).quantize(Decimal("0.01")))


# ----------------------------------------------------------------------------- seed bundle
def load_bundle():
    key = env("SEED_KEY", required=False)
    if not key or not os.path.exists(SEED_FILE):
        return None
    blob = json.load(open(SEED_FILE))
    raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    try:
        pt = AESGCM(raw).decrypt(b64d(blob["iv"]), b64d(blob["ct"]), None)
    except Exception:
        sys.exit("SEED_KEY does not match seed.enc.json")
    return json.loads(pt)


def division_map(bundle):
    divs = dict((bundle or {}).get("divisions", {}))
    for line in env("CUSTOMER_DIVISIONS", required=False).splitlines():
        parts = [p.strip() for p in line.replace("\t", ",").split(",") if p.strip()]
        if len(parts) >= 2 and parts[0].lower() not in ("customer id", "id"):
            divs[parts[0]] = parts[-1].upper()
    return divs


# ----------------------------------------------------------------------------- Sage REST
class Sage:
    def __init__(self):
        import requests
        self.base = os.environ.get("SAGE_API_BASE", "https://api.intacct.com/ia/api/v1").rstrip("/")
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"
        payload = {"grant_type": "client_credentials", "client_id": env("SAGE_CLIENT_ID"),
                   "client_secret": env("SAGE_CLIENT_SECRET"), "username": env("SAGE_USERNAME")}
        url = f"{self.base}/oauth2/token"
        r = self.s.post(url, json=payload, timeout=60)
        if r.status_code >= 400:
            r = self.s.post(url, data=payload, timeout=60)
        if r.status_code >= 400:
            sys.exit(f"Sage authentication failed ({r.status_code}): {r.text[:500]}")
        self.s.headers["Authorization"] = "Bearer " + r.json()["access_token"]

    def query(self, obj, fields, filters=None, required=True):
        """required=False: on failure, print a warning and return None so the run can fall back."""
        rows, start = [], 1
        while True:
            body = {"object": obj, "fields": fields, "start": start, "size": PAGE_SIZE}
            if filters:
                body["filters"] = filters
            r = self.s.post(f"{self.base}/services/core/query", json=body, timeout=120)
            if r.status_code >= 400:
                msg = f"Sage query on {obj} failed ({r.status_code}): {r.text[:500]}"
                if required:
                    sys.exit(msg)
                print("::warning::" + msg + " - using last known values instead")
                return None
            data = r.json()
            rows.extend(data.get("ia::result", []))
            nxt = (data.get("ia::meta") or {}).get("next")
            if not nxt:
                return rows
            start = int(nxt)


def g(rec, path, default=None):
    if path in rec:
        return rec[path]
    cur = rec
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def build_live(bundle, tz):
    s = Sage()
    items, names = [], {}

    for r in s.query("accounts-receivable/invoice",
                     ["invoiceNumber", "referenceNumber", "customer.id", "customer.name", "invoiceDate",
                      "dueDate", "totalBaseAmount", "totalBaseAmountDue"],
                     [{"$ne": {"totalBaseAmountDue": "0"}}]):
        bal = money(g(r, "totalBaseAmountDue"))
        c = g(r, "customer.id"); names[c] = g(r, "customer.name") or c
        items.append({"t": "cm" if bal < 0 else "inv", "n": g(r, "invoiceNumber") or "", "ref": g(r, "referenceNumber") or "",
                      "c": c, "d": g(r, "invoiceDate"), "due": g(r, "dueDate") or g(r, "invoiceDate"),
                      "amt": money(g(r, "totalBaseAmount")), "bal": bal})

    # unapplied cash: receipts with an open (unapplied) balance.
    # recordType "rp" = payment with an unapplied overpayment, "rr" = advance receipt.
    for r in s.query("accounts-receivable/payment",
                     ["id", "customer.id", "customer.name", "paidDate", "recordType", "baseCurrency.totalAmount", "baseCurrency.totalDue"],
                     [{"$ne": {"baseCurrency.totalDue": "0"}}]):
        c = g(r, "customer.id"); names[c] = g(r, "customer.name") or c; d = g(r, "paidDate")
        adv = str(g(r, "recordType") or "").lower() == "rr"
        items.append({"t": "adv" if adv else "op", "n": f"{'Advance' if adv else 'Overpayment'} {g(r, 'id')}", "ref": "",
                      "c": c, "d": d, "due": d,
                      "amt": -abs(money(g(r, "baseCurrency.totalAmount"))), "bal": -abs(money(g(r, "baseCurrency.totalDue")))})

    cust = s.query("accounts-receivable/customer", ["id", "name", "totalDue"], [{"$ne": {"totalDue": "0"}}])
    for r in cust:
        names.setdefault(g(r, "id"), g(r, "name"))
    check_total = round(sum(money(g(r, "totalDue")) for r in cust), 2)

    # mailing addresses for statements (customers with open items only)
    prev = ((bundle or {}).get("snapshot") or {}).get("customers") or {}
    addrs, ids = {}, sorted({x["c"] for x in items})
    ma = "contacts.default.mailingAddress."
    for i in range(0, len(ids), 200):
        rows = s.query("accounts-receivable/customer",
                       ["id", "contacts.default.printAs", ma + "addressLine1", ma + "addressLine2", ma + "city", ma + "state", ma + "postCode"],
                       [{"$in": {"id": ids[i:i + 200]}}], required=False)
        if rows is None:  # no access to contact fields: keep the addresses from the stored snapshot
            addrs.update({c: v["a"] for c, v in prev.items() if v.get("a")})
            break
        for r in rows:
            addrs[g(r, "id")] = [g(r, "contacts.default.printAs") or "", g(r, ma + "addressLine1") or "", g(r, ma + "addressLine2") or "",
                                 g(r, ma + "city") or "", g(r, ma + "state") or "", g(r, ma + "postCode") or ""]

    # cash receipts (payment history) for the last 2 years.
    # recordType: rp = customer payment, rr = advance receipt (both new cash);
    # ro = application of an earlier overpayment/advance (not new cash -> excluded).
    since = (dt.datetime.now(tz).date() - dt.timedelta(days=730)).isoformat()
    pays = s.query("accounts-receivable/payment",
                   ["id", "documentNumber", "referenceNumber", "paymentMethod", "paidDate", "state", "recordType",
                    "baseCurrency.totalAmount", "customer.id", "customer.name"],
                   [{"$gte": {"paidDate": since}}], required=False)
    if pays is None:
        return items, names, {c: v["lp"] for c, v in prev.items() if v.get("lp")}, check_total, addrs, None
    receipts = []
    for r in pays:
        st = str(g(r, "state") or "").lower()
        rt = str(g(r, "recordType") or "").lower()
        amt = money(g(r, "baseCurrency.totalAmount"))
        d = g(r, "paidDate")
        if amt <= 0 or not d or rt not in ("rp", "rr") or "revers" in st or "void" in st:
            continue
        c = g(r, "customer.id")
        names.setdefault(c, g(r, "customer.name") or c)
        ref = g(r, "documentNumber") or g(r, "referenceNumber") or ""
        receipts.append([d, c, amt, ref, g(r, "paymentMethod") or ("advance" if rt == "rr" else ""), str(g(r, "id") or "")])
    receipts.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # last cash receipt per customer (same-day receipts summed)
    by_day = {}
    for d, c, amt, *_ in receipts:
        by_day[(c, d)] = round(by_day.get((c, d), 0) + amt, 2)
    last_pay = {}
    for (c, d), amt in by_day.items():
        if c not in last_pay or d > last_pay[c][0]:
            last_pay[c] = [d, amt]

    return items, names, last_pay, check_total, addrs, receipts


# ----------------------------------------------------------------------------- sample
def build_sample():
    rnd = random.Random(7)
    today = dt.date.today()
    items, names, divs, last_pay = [], {}, {}, {}
    for i, name in enumerate(["Acme Plastics", "Bluebonnet Foods", "Cedar Freight", "Delta Polymers", "Evergreen Supply",
                              "Frontier Metals", "Granite Packaging", "Harbor Chemicals", "Ironwood Textiles", "Juniper Beverages"]):
        c = f"CUST{i + 1:03d}"; names[c] = name; divs[c] = "G1" if i % 3 == 0 else "G2"
        last_pay[c] = [(today - dt.timedelta(days=rnd.randint(2, 90))).isoformat(), round(rnd.uniform(200, 20000), 2)]
        for _ in range(rnd.randint(1, 8)):
            d = today - dt.timedelta(days=rnd.randint(0, 200)); amt = round(rnd.uniform(150, 30000), 2)
            items.append({"t": "inv", "n": f"I{9000 + len(items)}", "ref": "", "c": c, "d": d.isoformat(),
                          "due": (d + dt.timedelta(days=30)).isoformat(), "amt": amt, "bal": amt})
    receipts = []
    for k in range(220):
        c = f"CUST{rnd.randint(1, 10):03d}"
        receipts.append([(today - dt.timedelta(days=rnd.randint(0, 300))).isoformat(), c, round(rnd.uniform(100, 25000), 2),
                         str(rnd.randint(10000, 99999)), rnd.choice(["eft", "printedCheck"]), str(1000 + k)])
    receipts.sort(reverse=True)
    for d, c, amt, *_ in receipts:
        if c not in last_pay or d > last_pay[c][0]:
            last_pay[c] = [d, amt]
    return items, names, divs, last_pay, round(sum(x["bal"] for x in items), 2), receipts


# ----------------------------------------------------------------------------- assemble
def assemble(mode, items, names, divs, last_pay, check_total, tz, as_of=None, generated=None, addrs=None, entities=None, receipts=None):
    now = dt.datetime.now(tz)
    total = round(sum(x["bal"] for x in items), 2)
    ids = sorted({x["c"] for x in items})
    addrs = addrs or {}
    customers = {c: {"n": names.get(c) or c, "div": divs.get(c, "Unassigned"), "lp": last_pay.get(c), "a": addrs.get(c)} for c in ids}
    return {
        "v": 2, "mode": mode, "company": os.environ.get("COMPANY_NAME", "GFS Logistics"),
        "asOf": as_of or now.date().isoformat(), "generatedAt": generated or now.isoformat(timespec="seconds"),
        "publishedAt": now.isoformat(timespec="seconds"), "tz": str(tz),
        "total": total, "check": {"customerBalances": check_total, "ties": abs(total - check_total) < 0.005},
        "customers": customers, "items": items, "entities": entities or {},
        # payment history: [paidDate, customerId, amount, reference, method, id]; rcust = payers with no open items
        "receipts": receipts or [],
        "rcust": {c: {"n": names.get(c) or c, "div": divs.get(c, "Unassigned")}
                  for c in sorted({r[1] for r in (receipts or [])} - set(ids))},
    }


def encrypt_for_viewers(obj, password):
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, separators=(",", ":")).encode(), None)
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": PBKDF2_ITERATIONS, "salt": b64e(salt), "iv": b64e(iv), "ct": b64e(ct)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/data.enc.json")
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()
    tz = ZoneInfo(os.environ.get("REPORT_TZ", "America/Chicago"))

    if args.sample:
        items, names, divs, last_pay, check, receipts = build_sample()
        demo = lambda n, logo: {"name": n, "logo": logo, "address": ["100 Example St", "Dallas, TX 75201"],
                                "bank": {"Bank Name": "Example Bank", "Account Name": n, "Routing Number": "000000000", "Account Number": "0000000"},
                                "billcom": "https://example.com/pay"}
        addrs = {c: [names[c], "123 Main St", "", "Dallas", "TX", "75201"] for c in names}
        snap = assemble("sample", items, names, divs, last_pay, check, tz, addrs=addrs,
                        entities={"G1": demo("GFS Logistics", "logo.png"), "G2": demo("Texas Cartage Warehouse, LLC", "tcw-logo.png")},
                        receipts=receipts)
    else:
        bundle = load_bundle()
        divs = division_map(bundle)
        if env("SAGE_CLIENT_ID", required=False):
            items, names, last_pay, check, addrs, receipts = build_live(bundle, tz)
            for c, n in ((bundle or {}).get("names") or {}).items():
                names.setdefault(c, n)
            snap = assemble("live", items, names, divs, last_pay, check, tz, addrs=addrs,
                            entities=(bundle or {}).get("entities"), receipts=receipts)
            if receipts:
                today = dt.datetime.now(tz).date()
                m0 = today.strftime("%Y-%m")
                m1 = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
                tot = lambda m: (sum(1 for r in receipts if r[0][:7] == m), round(sum(r[2] for r in receipts if r[0][:7] == m), 2))
                print(f"[receipts] {len(receipts)} cash receipts since {receipts[-1][0]}; "
                      f"{m0}: {tot(m0)[0]} totaling {tot(m0)[1]:,.2f}; {m1}: {tot(m1)[0]} totaling {tot(m1)[1]:,.2f}")
        elif bundle:
            snap = bundle["snapshot"]
            for c, info in snap["customers"].items():  # apply any division overrides
                info["div"] = divs.get(c, info.get("div", "Unassigned"))
            snap["entities"] = bundle.get("entities") or snap.get("entities") or {}
            snap["mode"] = "snapshot"
            snap["publishedAt"] = dt.datetime.now(tz).isoformat(timespec="seconds")
        else:
            sys.exit("No Sage credentials and no SEED_KEY - nothing to publish. Use --sample for test data.")

    password = env("DASHBOARD_PASSWORD")
    if len(password) < 12:
        sys.exit("DASHBOARD_PASSWORD must be at least 12 characters.")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(encrypt_for_viewers(snap, password), open(args.out, "w"))

    unassigned = sorted(c for c, i in snap["customers"].items() if i["div"] == "Unassigned")
    print(f"[{snap['mode']}] {len(snap['items'])} open items, net AR {snap['total']:,.2f} - "
          f"{'ties' if snap['check']['ties'] else 'DOES NOT TIE'} to Sage customer balances {snap['check']['customerBalances']:,.2f}")
    if unassigned:
        print("::warning::Customers with no division (shown as Unassigned): " + ", ".join(unassigned))


if __name__ == "__main__":
    main()
