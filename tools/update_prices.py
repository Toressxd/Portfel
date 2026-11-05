"""Daily price update for every Portfel 60/40 user, run by GitHub Actions after the US close.
For each user with an app/plan document it rebuilds app/stan: the PLN price of every fund in the
plan, FX, the plan's indicators and a weekly PLN series of the benchmark (MSCI World).
Secrets (GitHub > Settings > Secrets and variables > Actions): SUPABASE_URL, SUPABASE_SERVICE_KEY.
Prices are Yahoo's free, delayed daily closes."""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
UA = {"User-Agent": "Mozilla/5.0"}
BENCHMARK = "IE00B4L5Y983"


def supa(method, path, body=None, prefer=None):
    req = urllib.request.Request(URL + path, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("apikey", KEY)
    if KEY.startswith("eyJ"):
        req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


_cache = {}


def fetch(symbol, rng="3mo"):
    if (symbol, rng) in _cache:
        return _cache[symbol, rng]
    out = None
    for host in ("query2", "query1"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range={rng}&interval=1d"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                res = json.load(r)["chart"]["result"][0]
        except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError):
            time.sleep(2)
            continue
        meta = res["meta"]
        off = meta.get("gmtoffset") or 0  # daily bars are stamped at local midnight
        day = lambda t: datetime.fromtimestamp(t + off, timezone.utc).date()
        pts = [(day(t), c) for t, c in zip(res.get("timestamp") or [], res["indicators"]["quote"][0]["close"]) if c is not None]
        live, live_t = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        if live and live_t and (not pts or day(live_t) > pts[-1][0]):
            pts.append((day(live_t), live))
        if pts:
            div = 100.0 if meta.get("currency") in ("GBp", "GBX") else 1.0
            out = {"currency": "GBP" if div == 100 else meta.get("currency"), "points": [(d, c / div) for d, c in pts]}
        break
    _cache[symbol, rng] = out
    time.sleep(0.3)
    return out


PREFERRED = ["GER", "LSE", "AMS", "MIL", "EBS", "PAR"]  # exchanges a Polish XTB investor typically buys on


def find_symbol(isin):
    """Yahoo symbol for an ISIN typed in the plan wizard (None when Yahoo does not know it)."""
    url = "https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode({"q": isin, "quotesCount": 10, "newsCount": 0})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            quotes = json.load(r).get("quotes", [])
    except (urllib.error.URLError, ValueError):
        return None
    quotes = [q for q in quotes if q.get("quoteType") in ("ETF", "EQUITY", "MUTUALFUND") and q.get("symbol")]
    quotes.sort(key=lambda q: PREFERRED.index(q["exchange"]) if q.get("exchange") in PREFERRED else 99)
    for q in quotes:
        if fetch(q["symbol"]):
            return q["symbol"]
    return None


"""Crisis test: how the plan's weights would have fared in three real crashes, in PLN.
Most UCITS funds are younger than 2008, so each fund stands in for a US-listed fund on the same index or theme
(prices with dividends). Unknown funds count as world equities."""
PROXY = {
    "IE00B4L5Y983": {"SPY": .7, "EFA": .3}, "IE00BK5BQT80": {"SPY": .6, "EFA": .3, "EEM": .1},
    "IE00B5BMR087": {"SPY": 1}, "IE00BKM4GZ66": {"EEM": 1}, "IE00B53SZB19": {"QQQ": 1}, "IE00BMC38736": {"SOXX": 1},
    "IE00BGV5VN51": {"QQQ": 1}, "IE00B43HR379": {"XLV": 1}, "IE000YYE6WK5": {"ITA": 1}, "IE00BJ5JPG56": {"FXI": 1},
    "IE000YU9K6K2": {"ITA": 1}, "IE00063FT9K6": {"XME": 1}, "IE0003Z9E2Y3": {"XME": 1}, "IE00B4ND3602": {"GLD": 1},
    "IE00B4NCWG09": {"SLV": 1}, "GB00B15KXQ89": {"HG=F": 1}, "IE00B5WHFQ43": {"EWW": 1},
}
WORLD = PROXY["IE00B4L5Y983"]
CRISES = [("Kryzys finansowy 2008", "2007-10-01", "2014-12-31"), ("Pandemia 2020", "2020-01-15", "2021-12-31"),
          ("Inflacja i stopy 2022", "2021-11-01", "2024-12-31")]
_hist = {}


def history(symbol):
    """Daily closes with dividends since mid-2007, {date: price}."""
    if symbol in _hist:
        return _hist[symbol]
    out = {}
    url = (f"https://query2.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?period1=1185926400"
           f"&period2={int(time.time())}&interval=1d")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            res = json.load(r)["chart"]["result"][0]
        closes = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or res["indicators"]["quote"][0]["close"]
        off = res["meta"].get("gmtoffset") or 0
        out = {datetime.fromtimestamp(t + off, timezone.utc).date().isoformat(): c for t, c in zip(res.get("timestamp") or [], closes) if c}
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError):
        pass
    _hist[symbol] = out
    time.sleep(0.3)
    return out


def crisis_test(plan):
    weights, used = {}, {}
    for r in plan.get("role", {}).values():
        funds = [i for i in r.get("fundusze", {}) if i not in plan.get("nie_dokupujemy", [])] or list(r.get("fundusze", {}))
        if not funds or not r.get("cel"):
            continue
        mix = PROXY.get(funds[0], WORLD)
        used[funds[0]] = "+".join(mix) + ("" if funds[0] in PROXY else " (brak historii: świat)")
        for s, w in mix.items():
            weights[s] = weights.get(s, 0) + r["cel"] / 100 * w
    fx = history("USDPLN=X")
    if not weights or not fx:
        return None

    def path(wts, start, end):
        series = {s: history(s) for s in wts}
        days = sorted(d for d in fx if start <= d <= end)
        last, base, out = {}, {}, []
        for d in days:  # carry the last known price over holidays
            for s, h in series.items():
                if d in h:
                    last[s] = h[d] * fx[d]
            if len(last) < len(wts):
                continue
            base = base or dict(last)
            out.append((d, sum(w * last[s] / base[s] for s, w in wts.items())))
        return out

    def drawdown(p):
        peak, peak_d, worst = p[0][1], p[0][0], (0, None, None, None)
        for d, v in p:
            if v > peak:
                peak, peak_d = v, d
            dd = v / peak - 1
            if dd < worst[0]:
                worst = (dd, peak_d, d, peak)
        dd, top, bottom, level = worst
        back = next((d for d, v in p if d > (bottom or "") and v >= level), None) if bottom else None
        months = lambda a, b: round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).days / 30.44)
        return {"spadek_proc": round(dd * 100, 1), "szczyt": top, "dolek": bottom, "powrot": back,
                "miesiace_spadku": months(top, bottom) if bottom else 0, "miesiace_powrotu": months(bottom, back) if back else None}

    out = []
    for name, start, end in CRISES:
        p, w = path(weights, start, end), path(WORLD, start, end)
        if len(p) > 20:
            out.append({"nazwa": name, **drawdown(p), "swiat_proc": drawdown(w)["spadek_proc"] if len(w) > 20 else None})
    return {"wyniki": out, "zamienniki": used, "uwaga": "W złotych, z dywidendami, według wag z planu. Zamienniki z USA o tym samym indeksie lub temacie."}


_yh = {}


def holdings(sym):
    """Top 10 companies and sector split of a fund (Yahoo quoteSummary, needs a session cookie and crumb).
    Commodity ETCs have none; any failure just leaves the fund without a breakdown."""
    try:
        if "op" not in _yh:
            import http.cookiejar
            op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36")]
            try:
                op.open("https://fc.yahoo.com", timeout=20)  # answers 404 but sets the cookie
            except urllib.error.URLError:
                pass
            _yh["op"], _yh["crumb"] = op, op.open("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=20).read().decode()
        url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(sym)}"
               f"?modules=topHoldings&crumb={urllib.parse.quote(_yh['crumb'])}")
        th = json.load(_yh["op"].open(url, timeout=20))["quoteSummary"]["result"][0]["topHoldings"]
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError):
        return None
    top = [{"s": h.get("symbol"), "n": h.get("holdingName") or h.get("symbol"), "p": round(h["holdingPercent"]["raw"] * 100, 2)}
           for h in th.get("holdings", []) if h.get("holdingPercent", {}).get("raw")]
    sek = {k: round(v["raw"] * 100, 2) for d in th.get("sectorWeightings", []) for k, v in d.items() if v.get("raw")}
    time.sleep(0.3)
    return {"top": top, "sektory": sek} if top or sek else None


def fund_risk(sym, rate):
    """For the fund card: worst fall from a peak and yearly volatility, in PLN, over up to 10 years of this listing."""
    q = fetch(sym, "10y")
    if not q or len(q["points"]) < 60:
        return None
    pts = [(d, c * rate(q["currency"], d)) for d, c in q["points"]]
    peak, peak_d, worst = pts[0][1], pts[0][0], (0.0, None, None)
    for d, v in pts:
        if v > peak:
            peak, peak_d = v, d
        if v / peak - 1 < worst[0]:
            worst = (v / peak - 1, peak_d, d)
    weekly = [v for i, (d, v) in enumerate(pts) if i % 5 == 0]
    rets = [b / a - 1 for a, b in zip(weekly, weekly[1:])]
    mean = sum(rets) / len(rets)
    vol = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 * 52 ** 0.5
    return {"od": str(pts[0][0]), "maks_spadek_proc": round(worst[0] * 100, 1), "maks_spadek_od": str(worst[1]),
            "maks_spadek_do": str(worst[2]), "zmiennosc_proc": round(vol * 100, 1)}


def back(points, n):
    return points[max(0, len(points) - 1 - n)]


def build(plan, fxd):
    def rate(cur, day):
        if cur == "PLN":
            return 1.0
        d = fxd[cur]
        if day in d:
            return d[day]
        earlier = [k for k in d if k <= day]
        return d[max(earlier)] if earlier else d[min(d)]

    ceny, braki = {}, []
    for isin, sym in plan.get("notowania", {}).items():
        q = fetch(sym)
        if not q or q["currency"] not in ("PLN", *fxd):
            braki.append(isin)
            continue
        p = q["points"]
        (ld, last), (wd, w), (md, m) = p[-1], back(p, 5), back(p, 21)
        pln = last * rate(q["currency"], ld)
        ceny[isin] = {"symbol": sym, "waluta": q["currency"], "cena": round(last, 4), "pln": round(pln, 4), "data": str(ld),
                      "tydz_proc": round((last / w - 1) * 100, 2),
                      "tydz_pln_proc": round((pln / (w * rate(q["currency"], wd)) - 1) * 100, 2),
                      "mies_pln_proc": round((pln / (m * rate(q["currency"], md)) - 1) * 100, 2)}
        risk = fund_risk(sym, rate)
        if risk:
            ceny[isin]["ryzyko"] = risk
        sk = holdings(sym)
        if sk:
            ceny[isin]["sklad"] = sk
    wsk = []
    for sym, label in plan.get("wskazniki", {}).items():
        q = fetch(sym)
        if q:
            p = q["points"]
            wsk.append({"symbol": sym, "nazwa": label, "wartosc": round(p[-1][1], 4), "data": str(p[-1][0]),
                        "tydz_proc": round((p[-1][1] / back(p, 5)[1] - 1) * 100, 2), "mies_proc": round((p[-1][1] / back(p, 21)[1] - 1) * 100, 2)})
    seria = []
    sym = plan.get("notowania", {}).get(BENCHMARK)
    q = fetch(sym, "10y") if sym else None
    if q:
        last_week = None
        for d, c in q["points"]:
            item = [str(d), round(c * rate(q["currency"], d), 4)]
            if d.isocalendar()[:2] == last_week:
                seria[-1] = item
            else:
                seria.append(item)
                last_week = d.isocalendar()[:2]
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Warsaw"))  # the runner is on UTC; the app shows Polish time
    except Exception:  # Windows without the tzdata package: the local clock is Polish anyway
        now = datetime.now()
    return {"asOf": now.strftime("%Y-%m-%d %H:%M"), "ceny": ceny, "bez_notowan": braki,
            "fx": {c: round(s[max(s)], 4) for c, s in fxd.items()}, "wskazniki": wsk,
            "benchmark": {"isin": BENCHMARK, "nazwa": "100% MSCI World", "seria": seria}}


def main():
    if not URL.startswith("https://"):
        print("BŁĄD: brak sekretu SUPABASE_URL (albo nie zaczyna się od https://). Dodaj go w Settings > Secrets and variables > Actions.")
        return 1
    if not KEY:
        print("BŁĄD: brak sekretu SUPABASE_SERVICE_KEY. Dodaj go w Settings > Secrets and variables > Actions.")
        return 1
    if not KEY.isascii() or " " in KEY:
        print("BŁĄD: SUPABASE_SERVICE_KEY zawiera znak, którego nie ma w kluczu (np. wielokropek …). "
              "Skopiuj pełny klucz przyciskiem kopiowania w Supabase, a nie jego skróconą, wyświetlaną wersję.")
        return 1
    try:
        plans = supa("GET", "/rest/v1/docs?select=user_id,data&path=eq.app/plan")
    except urllib.error.HTTPError as e:
        print(f"BŁĄD: Supabase odpowiedział {e.code}. 401/403 = zły klucz w SUPABASE_SERVICE_KEY (potrzebny klucz secret, nie publishable). {e.read()[:200]!r}")
        return 1
    except urllib.error.URLError as e:
        print(f"BŁĄD: nie mogę połączyć się z {URL}: {e.reason}. Sprawdź sekret SUPABASE_URL.")
        return 1
    if not plans:
        print("Brak planów, nic do zrobienia.")
        return 0
    fxd = {}
    for c in ("USD", "EUR", "GBP"):
        s = fetch(f"{c}PLN=X", "10y")
        if s:
            fxd[c] = dict(s["points"])
    ok = 0
    upsert = lambda uid, path, data: supa("POST", "/rest/v1/docs?on_conflict=user_id,path", [{"user_id": uid, "path": path, "data": data}],
                                          prefer="resolution=merge-duplicates,return=minimal")
    for row in plans:
        uid, plan = row["user_id"], row["data"]
        # funds added by ISIN in the wizard: find their Yahoo quote once and remember it in the plan
        isins = {i for r in plan.get("role", {}).values() for i in r.get("fundusze", {})}
        missing = [i for i in isins if i not in plan.get("notowania", {})]
        if missing:
            found = {i: s for i in missing if (s := find_symbol(i))}
            if found:
                plan.setdefault("notowania", {}).update(found)
                upsert(uid, "app/plan", plan)
            print(f"  nowe notowania: {found}, nie znaleziono: {[i for i in missing if i not in found]}")
        stan = build(plan, fxd)
        stan["kryzysy"] = crisis_test(plan)
        upsert(uid, "app/stan", stan)
        # opening positions saved before their fund had a price: turn the value into units now
        todo = supa("GET", f"/rest/v1/docs?select=path,data&user_id=eq.{uid}&path=like.transakcje/*&data->>do_przeliczenia=eq.true")
        for t in todo or []:
            d, c = t["data"], stan["ceny"].get(t["data"].get("isin"))
            if c and d.get("wartosc_otwarcia_pln"):
                d.update(jednostki=round(float(d["wartosc_otwarcia_pln"]) / c["pln"], 6), waluta=c["waluta"], do_przeliczenia=False)
                upsert(uid, t["path"], d)
        ok += 1
        print(f"użytkownik {row['user_id'][:8]}…: {len(stan['ceny'])} funduszy, braki {stan['bez_notowan']}")
    print(f"zaktualizowano {ok} portfeli")
    return 0


if __name__ == "__main__":
    sys.exit(main())
