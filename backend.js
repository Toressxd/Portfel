// Supabase backend for Portfel 60/40. It offers the same small document API the page used inside
// Claude (doc / collection / onSnapshot / set / update / delete / add), so the page code stays the same.
// All of the owner's documents are loaded at once (a few hundred rows), kept in memory, refreshed
// every 30 s and when the app comes back to the foreground.
(function () {
  const cfg = window.PORTFEL_CONFIG || {};
  const sb = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey, { auth: { persistSession: true, autoRefreshToken: true } });
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

  async function loadAll() {
    const rows = [];
    for (let from = 0; ; from += 1000) {
      const { data, error } = await sb.from("docs").select("path,data").range(from, from + 999);
      if (error) throw error;
      rows.push(...data);
      if (data.length < 1000) break;
    }
    store.clear();
    for (const r of rows) store.set(r.path, r.data);
    emitAll();
  }
  async function write(path, data) {
    const { error } = await sb.from("docs").upsert({ user_id: userId, path, data }, { onConflict: "user_id,path" });
    if (error) throw { code: "unavailable", message: error.message };
    store.set(path, data); emitPath(path);
  }
  const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  function doc(path) {
    return {
      id: path.split("/").pop(), path,
      get: async () => snap(path, store.get(path)),
      onSnapshot(cb) { if (!docListeners.has(path)) docListeners.set(path, new Set()); docListeners.get(path).add(cb); setTimeout(() => cb(snap(path, store.get(path))), 0); return () => docListeners.get(path).delete(cb); },
      set: data => write(path, data),
      update: data => write(path, { ...(store.get(path) || {}), ...data }),
      delete: async () => { const { error } = await sb.from("docs").delete().eq("user_id", userId).eq("path", path); if (error) throw { code: "unavailable", message: error.message }; store.delete(path); emitPath(path); },
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
        <h2>Portfel 60/40</h2><p class="note">Zaloguj się do swojego portfela. Dane widzisz tylko Ty.</p>
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
        const { data, error } = await sb.auth.signUp({ email, password });
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
      await loadAll();
      setInterval(() => { if (!document.hidden) loadAll().catch(() => {}); }, 30000);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) loadAll().catch(() => {}); });
      return { doc, collection };
    },
    async signOut() { await sb.auth.signOut(); location.reload(); },
    userEmail: async () => (await sb.auth.getUser()).data.user?.email || ""
  };
})();
