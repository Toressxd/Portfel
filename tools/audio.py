"""Audio versions of the reports, so they can be listened to like a podcast (in the car, with the screen off).
For every user: recent daily reports (dzienne/<date>, last 3 days) and weekly reports (raporty/<date>, last 10 days)
that have no audio yet, or whose text changed, are read aloud with a Polish neural voice (edge-tts), uploaded to the
private Supabase Storage bucket "audio" under <user id>/, and the report document gets
  audio: {url (signed link, 40 days), sekundy, glos, hash, wygasa}
Files older than 40 days are removed. Voice: app/ustawienia.glos = "zofia" (default) or "marek".
Run by GitHub Actions (audio.yml). Environment: SUPABASE_URL, SUPABASE_SERVICE_KEY."""
import asyncio, hashlib, json, os, re, sys, tempfile, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import URL, KEY, warsaw_today  # same settings and Supabase project

BUCKET = "audio"
VOICES = {"zofia": "pl-PL-ZofiaNeural", "marek": "pl-PL-MarekNeural"}
KEEP_DAYS = 40
MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia"]


def req(method, path, body=None, ctype="application/json", extra=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    r = urllib.request.Request(URL + path, method=method, data=data)
    r.add_header("apikey", KEY)
    if KEY.startswith("eyJ"):
        r.add_header("Authorization", "Bearer " + KEY)
    r.add_header("Content-Type", ctype)
    for k, v in (extra or {}).items():
        r.add_header(k, v)
    with urllib.request.urlopen(r, timeout=60) as res:
        raw = res.read()
    return json.loads(raw) if raw else None


def ensure_bucket():
    if not any(b.get("id") == BUCKET for b in req("GET", "/storage/v1/bucket") or []):
        req("POST", "/storage/v1/bucket", {"id": BUCKET, "name": BUCKET, "public": False, "file_size_limit": 25 * 1024 * 1024,
                                           "allowed_mime_types": ["audio/mpeg"]})
        print("utworzono prywatny bucket audio")


def spoken(doc, kind):
    """Markdown report -> text that sounds natural when read aloud."""
    d = doc.get("data", "")
    try:
        dd = date.fromisoformat(d)
        when = f"{dd.day} {MONTHS[dd.month - 1]}"
    except ValueError:
        when = d
    intro = f"{'Prasówka' if kind == 'dzienne' else 'Raport tygodnia'}, {when}. NKU."
    lines, skip = [], False
    for raw in (doc.get("tresc") or "").splitlines():
        line = raw.strip()
        if re.match(r"^#{1,4}\s", line):
            head = re.sub(r"^#+\s*", "", line).strip()
            skip = head.lower().startswith(("źródła", "zrodla", "źródło"))
            if not skip:
                lines.append("\n" + head.rstrip(".:") + ".")
            continue
        if skip or not line or re.match(r"^\|?\s*:?-{3,}", line):
            continue
        if line.startswith("|"):  # table row: cells joined with commas
            line = ", ".join(c.strip() for c in line.strip("|").split("|") if c.strip())
        line = re.sub(r"^[-*•]\s+|^\d+\.\s+", "", line)
        lines.append(line if re.search(r"[.!?:;]$", line) else line + ".")
    t = "\n".join(lines)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)          # links -> their text
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`>#]", "", t)
    t = re.sub(r"(?<=\d)[   ](?=\d{3}\b)", "", t)  # 21 812 -> 21812
    t = re.sub(r"\bUSD/PLN\b", "kurs dolara", t)
    t = re.sub(r"\bEUR/PLN\b", "kurs euro", t)
    t = t.replace("±", " plus minus ")
    t = re.sub(r"(?<=\d)\s?[–−-]\s?(?=\d)", " do ", t)       # 3,75–4,00% -> 3,75 do 4,00%
    t = re.sub(r"[−–](?=\s?\d)", "minus ", t)
    t = re.sub(r"\+(?=\s?\d)", "plus ", t)
    for a, b in [(r"\bpp\b", "punktu procentowego"), (r"\bp\.p\.", "punktu procentowego"), (r"\bvs\.?\b", "wobec"),
                 (r"\bm/m\b", "miesiąc do miesiąca"), (r"\br/r\b", "rok do roku"), (r"\bmld\b", "miliardów"), (r"\bmln\b", "milionów"),
                 (r"\btys\.", "tysięcy"), (r"\bok\.", "około"), (r"\bnp\.", "na przykład"), (r"\bm\.in\.", "między innymi"),
                 (r"\bUSD\b", "dolarów"), (r"\bEUR\b", "euro"), (r"→", ", potem "), (r"\s*/\s*", " na ")]:
        t = re.sub(a, b, t)
    outro = "\nTo wszystko na dziś. Jeden dzień to szum przy Twoim horyzoncie." if kind == "dzienne" else "\nTo wszystko w tym tygodniu. Do usłyszenia."
    return say(intro + "\n" + re.sub(r"\n{3,}", "\n\n", t).strip() + outro)


def say(t):
    """Finance shorthand pronounced the way a Polish speaker says it; paragraphs kept (they give the voice a breath)."""
    for a, b in [(r"S&P\s?500", "es-en-pi pięćset"), (r"\bMSCI\b", "M S C I"), (r"\bETF", "E T F"), (r"\bTSMC\b", "T S M C"),
                 (r"\bEBC\b", "E B C"), (r"\bNBP\b", "N B P"), (r"\bRPP\b", "R P P"), (r"\bPKB\b", "P K B"), (r"\bXTB\b", "X T B"),
                 (r"(?<=\d)[   ](?=\d{3}\b)", ""), (r"[ \t]+", " ")]:
        t = re.sub(a, b, t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def script(doc, kind):
    """What the voice says: the spoken version Claude wrote for listening (audio_tekst: like a host talking,
    not a page read aloud) or, for an older report without one, the Markdown turned into sentences."""
    if (doc.get("audio_tekst") or "").strip():
        return say(doc["audio_tekst"])
    return spoken(doc, kind)


async def speak(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate="+0%").save(path)


def main():
    if not (URL and KEY):
        print("Brak SUPABASE_URL / SUPABASE_SERVICE_KEY: pomijam audio.")
        return 0
    ensure_bucket()
    today = warsaw_today()
    rows = req("GET", "/rest/v1/docs?select=user_id,path,data&or=(path.like.dzienne/*,path.like.raporty/*)&limit=5000") or []
    ust = {r["user_id"]: r["data"] or {} for r in req("GET", "/rest/v1/docs?select=user_id,data&path=eq.app/ustawienia") or []}
    made = 0
    for r in rows:
        uid, path, doc = r["user_id"], r["path"], r["data"] or {}
        kind = path.split("/")[0]
        recent = (today - timedelta(days=3 if kind == "dzienne" else 10)).isoformat()
        if doc.get("data", "") < recent or not doc.get("tresc"):
            continue
        voice = VOICES.get((ust.get(uid) or {}).get("glos"), VOICES["zofia"])
        text = script(doc, kind)
        h = hashlib.sha1((voice + text).encode()).hexdigest()[:12]
        if (doc.get("audio") or {}).get("hash") == h:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "a.mp3")
            try:
                asyncio.run(speak(text, voice, f))
            except Exception as e:
                print(f"  {path}: synteza nie wyszła: {e}")
                continue
            data = open(f, "rb").read()
        obj = f"{uid}/{kind}/{doc['data']}.mp3"
        req("POST", f"/storage/v1/object/{BUCKET}/{urllib.parse.quote(obj)}", data, "audio/mpeg", {"x-upsert": "true", "cache-control": "3600"})
        signed = req("POST", f"/storage/v1/object/sign/{BUCKET}/{urllib.parse.quote(obj)}", {"expiresIn": KEEP_DAYS * 86400})
        link = signed.get("signedURL") or signed.get("signedUrl")
        doc["audio"] = {"url": URL + "/storage/v1" + link, "sekundy": round(len(data) * 8 / 48000), "glos": voice, "hash": h,
                        "wygasa": (today + timedelta(days=KEEP_DAYS - 1)).isoformat()}
        req("POST", "/rest/v1/docs?on_conflict=user_id,path", [{"user_id": uid, "path": path, "data": doc}], extra={"Prefer": "resolution=merge-duplicates,return=minimal"})
        made += 1
        print(f"użytkownik {uid[:8]}…: {path} ({doc['audio']['sekundy']} s)")
    # remove files past their link's life
    old = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for uid in {r["user_id"] for r in rows}:
        gone = []
        for kind in ("dzienne", "raporty"):
            for o in req("POST", f"/storage/v1/object/list/{BUCKET}", {"prefix": f"{uid}/{kind}/", "limit": 500}) or []:
                if o.get("name", "")[:10] < old:
                    gone.append(f"{uid}/{kind}/{o['name']}")
        if gone:
            req("DELETE", f"/storage/v1/object/{BUCKET}", {"prefixes": gone})
            print(f"usunięto {len(gone)} starych plików")
    print(f"nagrano {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
