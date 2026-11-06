// Supabase backend for NKU (Portfel 60/40). It offers the same small document API the page used inside
// Claude (doc / collection / onSnapshot / set / update / delete / add), so the page code stays the same.
// All of the owner's documents are loaded at once (a few hundred rows), kept in memory, refreshed
// every 30 s and when the app comes back to the foreground.
(function () {
  const cfg = window.PORTFEL_CONFIG || {};
  const sb = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey, { auth: { persistSession: true, autoRefreshToken: true }, global: { fetch: (...a) => window.fetch(...a) } });
  const store = new Map();          // path -> data
  const docListeners = new Map();   // path -> Set(cb)
  const colListeners = new Set();   // {path, order, dir, lim, cb}
  let userId = null;

  const snap = (path, data) => ({ id: path.split("/").pop(), exists: data !== undefined, data: () => data, metadata: { fromCache: false, hasPendingWrites: false } });
  const parentOf = path => path.split("/").slice(0, -1).join("/");
  function emitDoc(path) { for (const cb of docListeners.get(path) || []) cb(snap(path, store.get(path))); }
  function emitCol(l) {
    let rows = [...store.entries()].filter(([p]) => parentOf(p) === l.path).map(([p, d]) => snap(p, d));
    if (l.order) rows.sort((a, b) => { const x = a.data()[l.order], y = b.data()[l.order]; const c = x == null ? 1 : y == null ? -1 : x < y ? -1 : x > y ? 1 : 0; return l.dir === "desc" ? -c : c; });
    if (l.lim) rows = rows.slice(0, l.lim);
    l.cb({ docs: rows, size: rows.length, empty: !rows.length, docChanges: () => [], metadata: { fromCache: false, hasPendingWrites: false } });
  }
  function emitAll() { for (const p of docListeners.keys()) emitDoc(p); for (const l of colListeners) emitCol(l); }
  function emitPath(path) { emitDoc(path); const par = parentOf(path); for (const l of colListeners) if (l.path === par) emitCol(l); }

  /* Nothing typed may vanish:
     - a refresh that started before a save must not wipe that save from the screen (recent local writes are laid back on top);
     - a save made offline, or with a session that expired in the background, waits in an outbox on this device
       (kept across reloads and app updates) and is sent as soon as the connection is back. */
  let gen = 0, loading = null;
  const recent = [];                                    // {gen, t, path, data} (data undefined = deleted)
  const OUTBOX = () => "nku-outbox-" + userId;
  const readBox = () => { try { return JSON.parse(localStorage.getItem(OUTBOX()) || "[]"); } catch (e) { return []; } };
  const saveBox = box => { try { localStorage.setItem(OUTBOX(), JSON.stringify(box)); } catch (e) {} };
  function remember(path, data) {
    recent.push({ gen: ++gen, t: Date.now(), path, data });
    while (recent.length && Date.now() - recent[0].t > 120000) recent.shift();
  }
  function overlay(fromGen) {
    for (const op of recent) if (op.gen > fromGen) { if (op.data === undefined) store.delete(op.path); else store.set(op.path, op.data); }
    for (const op of readBox()) { if (op.del) store.delete(op.path); else store.set(op.path, op.data); }
  }
  const offline = err => !navigator.onLine || !err?.code && /fetch|network|load failed|timeout/i.test(err?.message || "");
  const authErr = err => /jwt|token|expired|401/i.test((err?.message || "") + (err?.code || ""));

  async function loadAll() {
    if (loading) return loading;
    loading = (async () => {
      await flush();
      const g0 = gen, rows = [];
      for (let from = 0; ; from += 1000) {
        const { data, error } = await sb.from("docs").select("path,data").order("path").range(from, from + 999);
        if (error) throw error;
        rows.push(...data);
        if (data.length < 1000) break;
      }
      store.clear();
      for (const r of rows) store.set(r.path, r.data);
      overlay(g0);
      emitAll();
    })();
    try { await loading; } finally { loading = null; }
  }
  async function send(op) {
    const q = op.del ? sb.from("docs").delete().eq("user_id", userId).eq("path", op.path)
                     : sb.from("docs").upsert({ user_id: userId, path: op.path, data: op.data }, { onConflict: "user_id,path" });
    let { error } = await q;
    if (error && authErr(error)) { await sb.auth.refreshSession().catch(() => {}); ({ error } = await (op.del ? sb.from("docs").delete().eq("user_id", userId).eq("path", op.path) : sb.from("docs").upsert({ user_id: userId, path: op.path, data: op.data }, { onConflict: "user_id,path" }))); }
    return error;
  }
  async function flush() {
    let box = readBox();
    if (!box.length || !navigator.onLine) return;
    for (const op of [...box]) {
      const error = await send(op).catch(e => e);
      if (error && offline(error)) return;            // still no connection: try again later
      box = readBox().filter(x => !(x.path === op.path && x.t === op.t)); saveBox(box);
      if (error) console.warn("NKU: nie zapisano", op.path, error.message);
    }
  }
  async function apply(op) {
    remember(op.path, op.del ? undefined : op.data);
    if (op.del) store.delete(op.path); else store.set(op.path, op.data);
    emitPath(op.path);
    const error = await send(op).catch(e => e);
    if (!error) return;
    if (offline(error) || authErr(error)) { saveBox([...readBox().filter(x => x.path !== op.path), { ...op, t: Date.now() }]); return; } // kept, sent later
    loadAll().catch(() => {});                          // refused for good: show the real state again
    throw { code: "unavailable", message: error.message };
  }
  const write = (path, data) => apply({ path, data });
  const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  function doc(path) {
    return {
      id: path.split("/").pop(), path,
      get: async () => snap(path, store.get(path)),
      onSnapshot(cb) { if (!docListeners.has(path)) docListeners.set(path, new Set()); docListeners.get(path).add(cb); setTimeout(() => cb(snap(path, store.get(path))), 0); return () => docListeners.get(path).delete(cb); },
      set: data => write(path, data),
      update: data => write(path, { ...(store.get(path) || {}), ...data }),
      delete: () => apply({ path, del: true }),
      collection: sub => collection(path + "/" + sub)
    };
  }
  function collection(path, order = null, dir = "asc", lim = 0) {
    const q = {
      path,
      orderBy: (f, d = "asc") => collection(path, f, d, lim),
      limit: n => collection(path, order, dir, n),
      where: () => q,
      get: async () => { const out = []; emitCol({ path, order, dir, lim, cb: s => out.push(s) }); return out[0]; },
      onSnapshot(cb) { const l = { path, order, dir, lim, cb }; colListeners.add(l); setTimeout(() => emitCol(l), 0); return () => colListeners.delete(l); },
      doc: id => doc(path + "/" + (id || newId())),
      add: async data => { const d = doc(path + "/" + newId()); await d.set(data); return d; }
    };
    return q;
  }

  // sign-in screen, shown only when there is no saved session on this device
  function loginScreen() {
    return new Promise(resolve => {
      const box = document.createElement("div");
      box.className = "sheet-bg";
      box.innerHTML = `<div class="sheet" role="dialog" aria-modal="true" aria-label="Logowanie" style="max-width:420px">
        <h2>NKU</h2><p class="note">Zaloguj się do swojego portfela. Dane widzisz tylko Ty.</p>
        <form id="lg" style="display:grid;gap:12px">
          <div class="field"><label for="lg-mail">E-mail</label><input id="lg-mail" type="email" autocomplete="email" required></div>
          <div class="field"><label for="lg-pass">Hasło</label><input id="lg-pass" type="password" autocomplete="current-password" minlength="8" required></div>
          <div class="actions"><button class="btn" type="submit" style="flex:1">Zaloguj</button><button class="btn ghost" type="button" id="lg-new">Załóż konto</button></div>
          <p class="note" id="lg-msg" role="status"></p>
        </form></div>`;
      document.body.appendChild(box);
      const msg = t => { box.querySelector("#lg-msg").textContent = t; };
      box.querySelector("#lg").addEventListener("submit", async e => {
        e.preventDefault(); msg("Loguję…");
        const { data, error } = await sb.auth.signInWithPassword({ email: box.querySelector("#lg-mail").value.trim(), password: box.querySelector("#lg-pass").value });
        if (error) return msg("Nie udało się zalogować: sprawdź e-mail i hasło.");
        box.remove(); resolve(data.user);
      });
      box.querySelector("#lg-new").addEventListener("click", async () => {
        const email = box.querySelector("#lg-mail").value.trim(), password = box.querySelector("#lg-pass").value;
        if (!email || password.length < 8) return msg("Wpisz e-mail i hasło (min. 8 znaków), potem kliknij Załóż konto.");
        msg("Zakładam konto…");
        const { data, error } = await sb.auth.signUp({ email, password, options: { emailRedirectTo: location.origin + location.pathname } });
        if (error) return msg("Nie udało się założyć konta: " + error.message);
        if (!data.session) return msg("Konto założone. Kliknij link w e-mailu od Supabase, potem zaloguj się tutaj.");
        box.remove(); resolve(data.user);
      });
    });
  }

  window.PortfelBackend = {
    async connect() {
      if (!cfg.supabaseUrl || cfg.supabaseUrl.startsWith("WKLEJ")) throw new Error("Brak konfiguracji Supabase w config.js.");
      const { data } = await sb.auth.getSession();
      const user = data.session?.user || await loginScreen();
      userId = user.id;
      try { await loadAll(); }
      catch (e) { if (!readBox().length) throw e; overlay(gen); emitAll(); }  // offline start: show what waits to be sent
      setInterval(() => { if (!document.hidden) loadAll().catch(() => {}); }, 30000);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) loadAll().catch(() => {}); });
      addEventListener("online", () => loadAll().catch(() => {}));
      return { doc, collection };
    },
    async signOut() { await sb.auth.signOut(); location.reload(); },
    userEmail: async () => (await sb.auth.getUser()).data.user?.email || ""
  };
})();
