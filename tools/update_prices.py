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
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Warsaw"))  # the runner is on UTC; the app shows Polish time
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
    for row in plans:
        stan = build(row["data"], fxd)
        supa("POST", "/rest/v1/docs?on_conflict=user_id,path", [{"user_id": row["user_id"], "path": "app/stan", "data": stan}],
             prefer="resolution=merge-duplicates,return=minimal")
        ok += 1
        print(f"użytkownik {row['user_id'][:8]}…: {len(stan['ceny'])} funduszy, braki {stan['bez_notowan']}")
    print(f"zaktualizowano {ok} portfeli")
    return 0


if __name__ == "__main__":
    sys.exit(main())
