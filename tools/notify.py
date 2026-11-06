"""Push notifications for every NKU user with a subscribed device (documents push/<device>).
Sends each message once (log in app/push_log) and only the kinds the user left on in app/ustawienia.powiadomienia:
  raporty  a new daily report today, a new weekly report
  terminy  bond interest within 3 days, IKE limit left in December, plan review date reached
Run by GitHub Actions in the morning and by the report task on the owner's PC right after the reports are saved.
Environment: SUPABASE_URL, SUPABASE_SERVICE_KEY, VAPID_PRIVATE_KEY (or the PORTFEL_* names on the owner's PC)."""
import json, os, sys, urllib.error, urllib.request
from datetime import date, datetime, timedelta


def setting(*names):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    if os.name == "nt":  # owner's PC: the values are saved as user variables
        import winreg
        for n in names:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                    return winreg.QueryValueEx(k, n)[0].strip()
            except OSError:
                pass
    return ""


URL = setting("SUPABASE_URL", "PORTFEL_SUPABASE_URL").rstrip("/")
KEY = setting("SUPABASE_SERVICE_KEY", "PORTFEL_SUPABASE_SERVICE_KEY")
VAPID = setting("VAPID_PRIVATE_KEY", "PORTFEL_VAPID_PRIVATE_KEY")
APP = "https://toressxd.github.io/Portfel/"


def supa(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("apikey", KEY)
    if KEY.startswith("eyJ"):
        req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Content-Type", "application/json")
    if method == "POST":
        req.add_header("Prefer", "resolution=merge-duplicates,return=minimal")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def warsaw_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Warsaw")).date()
    except Exception:
        return date.today()


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 February
        return d.replace(year=d.year + n, day=28)


def messages(docs, today):
    """(key, kind, title, body, url) the user should get today."""
    out = []
    dz = sorted((d for p, d in docs.items() if p.startswith("dzienne/")), key=lambda d: d.get("data", ""))
    if dz and dz[-1].get("data") == today.isoformat():
        d = dz[-1]
        first = next((l.lstrip("-* ").strip() for l in (d.get("podsumowanie") or d.get("tresc") or "").splitlines() if l.strip().lstrip("-*# ").strip()), "")
        out.append((f"dz:{d['data']}", "raporty", "Raport dnia", first[:180] or "Nowy raport w aplikacji.", APP + "#raporty"))
    rt = sorted((d for p, d in docs.items() if p.startswith("raporty/")), key=lambda d: d.get("data", ""))
    if rt and rt[-1].get("data", "") >= (today - timedelta(days=2)).isoformat():
        d = rt[-1]
        out.append((f"rt:{d['data']}", "raporty", d.get("tytul") or "Raport tygodnia", "Portfel, rynki i Twoje tezy z ostatniego tygodnia.", APP + "#raporty"))
    cele = docs.get("app/cele") or {}
    for o in cele.get("obligacje") or []:
        try:
            buy = date.fromisoformat(o.get("zakup") or "")
        except ValueError:
            continue
        for n in range(1, 5):
            due = add_years(buy, n)
            if today <= due <= today + timedelta(days=3):
                out.append((f"obl:{o.get('seria')}:{due}", "terminy", f"Odsetki z {o.get('seria') or 'obligacji'}",
                            f"{due.strftime('%d.%m')} wpłyną odsetki. Zdecyduj, gdzie je odłożyć.", APP + "#cele"))
    lim = cele.get("limity") or {}
    if today.month == 12 and today.day in (1, 15, 27) and str(lim.get("rok")) == str(today.year) and lim.get("ike"):
        paid = sum(float(d.get("kwota_pln") or 0) for p, d in docs.items() if p.startswith("transakcje/") and d.get("konto") == "IKE"
                   and str(d.get("data", "")).startswith(str(today.year)) and not str(d.get("typ", "")).startswith("sprz") and not d.get("otwarcie"))
        left = float(lim["ike"]) - max(paid, float(lim.get("ike_wplacone") or 0))
        if left > 0:
            out.append((f"ike:{today}", "terminy", "Limit IKE przepada 31 grudnia", f"Zostało {left:,.0f} zł limitu na {today.year}.".replace(",", " "), APP + "#cele"))
    prz = ((docs.get("app/plan") or {}).get("profil") or {}).get("przeglad")
    if prz and prz == today.isoformat():
        out.append((f"prz:{prz}", "terminy", "Czas na przegląd planu", "Sprawdź cel, wagi i tezy. Zmieniaj tylko z ważnego powodu.", APP))
    return out


def main():
    if not (URL and KEY and VAPID):
        print("Brak SUPABASE_URL / SUPABASE_SERVICE_KEY / VAPID_PRIVATE_KEY: pomijam powiadomienia.")
        return 0
    from pywebpush import webpush, WebPushException
    subs = supa("GET", "/rest/v1/docs?select=user_id,path,data&path=like.push/*") or []
    users = {}
    for s in subs:
        users.setdefault(s["user_id"], []).append(s)
    today, sent = warsaw_today(), 0
    for uid, devices in users.items():
        docs = {r["path"]: r["data"] for r in supa("GET", f"/rest/v1/docs?select=path,data&user_id=eq.{uid}&limit=5000")}
        pw = (docs.get("app/ustawienia") or {}).get("powiadomienia") or {"raporty": True, "terminy": True}
        log = (docs.get("app/push_log") or {}).get("wyslane") or {}
        todo = [m for m in messages(docs, today) if m[0] not in log and pw.get(m[1], True)]
        for key, kind, title, body, url in todo:
            payload = json.dumps({"title": title, "body": body, "url": url, "tag": key.split(":")[0]})
            for dev in devices:
                try:
                    webpush(subscription_info=dev["data"]["sub"], data=payload, vapid_private_key=VAPID, vapid_claims={"sub": "https://toressxd.github.io"}, ttl=12 * 3600)
                    sent += 1
                except WebPushException as e:
                    if e.response is not None and e.response.status_code in (404, 410):  # the phone dropped this subscription
                        supa("DELETE", f"/rest/v1/docs?user_id=eq.{uid}&path=eq.{urllib.request.quote(dev['path'])}")
                    else:
                        print(f"  błąd wysyłki: {e}")
            log[key] = today.isoformat()
        if todo:
            keep = {k: v for k, v in log.items() if v >= (today - timedelta(days=60)).isoformat()}
            supa("POST", "/rest/v1/docs?on_conflict=user_id,path", [{"user_id": uid, "path": "app/push_log", "data": {"wyslane": keep}}])
        print(f"użytkownik {uid[:8]}…: urządzeń {len(devices)}, wiadomości {len(todo)}")
    print(f"wysłano {sent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
