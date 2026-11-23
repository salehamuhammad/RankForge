// Shared helpers for every page. Set the API URL in config.js.
const API = (window.RANKFORGE_API || "http://localhost:7860").replace(/\/$/, "");
const auth = {
  get user() { return localStorage.getItem("rf_user") || ""; },
  get token() { return localStorage.getItem("rf_token") || ""; },
  set(u, t) { localStorage.setItem("rf_user", u); localStorage.setItem("rf_token", t); },
  clear() { localStorage.removeItem("rf_user"); localStorage.removeItem("rf_token"); },
};

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (auth.token) headers.Authorization = "Bearer " + auth.token;
  const r = await fetch(API + path, { ...opts, headers });
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) { auth.clear(); openLogin(); throw new Error("Please log in"); }
  if (!r.ok) throw new Error(data.detail || data.reply || r.statusText);
  return data;
}

function md(text) {
  const html = window.marked ? marked.parse(text || "") : (text || "").replace(/</g, "&lt;").replace(/\n/g, "<br>");
  return window.DOMPurify ? DOMPurify.sanitize(html) : html;
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function navbar(active) {
  const links = [["index.html", "Home"], ["new.html", "New article"], ["blog.html", "Live blog"], ["admin.html", "Dashboard"]];
  document.body.insertAdjacentHTML("afterbegin", `
  <header class="nav"><a class="logo" href="index.html">Rank<span>Forge</span></a>
    <nav>${links.map(([h, t]) => `<a href="${h}" class="${h === active ? "active" : ""}">${t}</a>`).join("")}</nav>
    <span class="who" id="who"></span></header>
  <div class="modal" id="loginModal"><div class="card"><h3 style="margin-top:0">Log in to your business</h3>
    <p class="muted" style="font-size:13px">New business id? It is created on first login.</p>
    <label>Business id</label><input id="lu" placeholder="e.g. daraz-laptop-hub">
    <label>Password</label><input id="lp" type="password" placeholder="at least 4 characters">
    <p id="lerr" style="color:var(--bad);font-size:13px"></p>
    <div class="row"><button class="btn" onclick="doLogin()">Continue</button><button class="btn ghost" onclick="closeLogin()">Cancel</button></div>
  </div></div>`);
  renderWho();
}
function renderWho() {
  const w = document.getElementById("who");
  w.innerHTML = auth.user ? `Signed in as <b>${esc(auth.user)}</b> · <a href="#" onclick="auth.clear();location.reload()">Log out</a>`
    : `<a class="btn sm" href="#" onclick="openLogin()">Log in</a>`;
}
function openLogin() { document.getElementById("loginModal").classList.add("open"); }
function closeLogin() { document.getElementById("loginModal").classList.remove("open"); }
async function doLogin() {
  const user_id = document.getElementById("lu").value.trim(), password = document.getElementById("lp").value;
  try {
    const r = await api("/auth/login", { method: "POST", body: JSON.stringify({ user_id, password }) });
    auth.set(r.user_id, r.token); closeLogin(); renderWho(); window.onLogin && window.onLogin();
  } catch (e) { document.getElementById("lerr").textContent = e.message; }
}
function requireLogin() { if (!auth.user) { openLogin(); return false; } return true; }
