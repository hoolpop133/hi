import http.server
import socketserver
import os
import json
import time
import threading
import webbrowser
import hashlib
import traceback
import socket

# ================= НАСТРОЙКИ =================
PORT = int(os.environ.get("PORT", 8009))
JSON_SKINS  = "skins.json"
JSON_USERS  = "users.json"
JSON_CHAT   = "chat.json"
JSON_CHAT_HVH = "chat_hvh.json"
JSON_LOGS   = "logs.json"
JSON_PROMO  = "promos.json"
JSON_STATS  = "stats.json"
JSON_NEWS   = "news.json"

CURRENT_VERSION = "5.5.0"
VERSION_SUFFIX  = "XP"
SKIN_LIFETIME   = 24 * 60 * 60
START_BALANCE   = 100

def calc_level(xp):
    if xp < 10000: return 1
    return 1 + (xp - 10000) // 1000

XP_REWARDS = {
    "case_open": 1, "upgrade_win": 10, "upgrade_fail": 2,
    "sell_skin": 1, "daily": 5, "chat_msg": 1,
    "promo": 3, "friend_accept": 2, "extend_skin": 2
}

EXAMPLE_USERS = {
    "admin": {"password": hashlib.sha256("admin".encode()).hexdigest(), "role": "admin", "balance": 999999999,
              "xp": 0, "level": 1, "inventory": [], "friends": [], "incoming": [], "outgoing": [], "inbox": [],
              "muted": False, "banned": False, "daily_last": 0, "stats": {}, "sponsor": 0, "hvh": False},
    "betatester": {"password": hashlib.sha256("beta".encode()).hexdigest(), "role": "betatester", "balance": 999999999,
              "xp": 0, "level": 1, "inventory": [], "friends": [], "incoming": [], "outgoing": [], "inbox": [],
              "muted": False, "banned": False, "daily_last": 0, "stats": {}, "sponsor": 0, "hvh": False},
    "gl_moder": {"password": hashlib.sha256("glmoder".encode()).hexdigest(), "role": "gl_moder", "balance": 50000,
              "xp": 0, "level": 1, "inventory": [], "friends": [], "incoming": [], "outgoing": [], "inbox": [],
              "muted": False, "banned": False, "daily_last": 0, "stats": {}, "sponsor": 0, "hvh": False},
    "moder": {"password": hashlib.sha256("moder".encode()).hexdigest(), "role": "moder", "balance": 10000,
              "xp": 0, "level": 1, "inventory": [], "friends": [], "incoming": [], "outgoing": [], "inbox": [],
              "muted": False, "banned": False, "daily_last": 0, "stats": {}, "sponsor": 0, "hvh": False}
}

EXAMPLE_PROMOS = {"WELCOME": {"amount": 500, "uses": 0, "limit": 100}}
DEFAULT_NEWS = [
    {"id": 36, "version": "5.5.0 XP", "date": "2026-11-06",
     "title": "🛠 Только сервер",
     "text": "Убрана автогенерация. Теперь сервер читает готовый skins.json.",
     "badge": "NEW"},
]

LOADING_FACTS = [
    "🖼 Картинки грузятся с CDN Steam.",
    "🏆 Уровень = +1000 XP сверх 10000.",
    "💎 Спонсор 1-10 уровня виден всем.",
    "🚀 HvH — отдельный мир с своими админами.",
]

ROLE_META = {
    "admin": {"label": "ADMIN", "icon": "👑", "color": "#ef4444"},
    "betatester": {"label": "BETA TESTER", "icon": "🧪", "color": "#22d3ee"},
    "gl_moder": {"label": "GL MODER", "icon": "⭐", "color": "#f59e0b"},
    "moder": {"label": "MODER", "icon": "🛡", "color": "#eab308"},
    "hvh_admin": {"label": "HVH ADMIN", "icon": "🔥", "color": "#dc2626"},
    "hvh_moder": {"label": "HVH MODER", "icon": "💀", "color": "#7f1d1d"},
    "hvh_sponsor": {"label": "HVH SPONSOR", "icon": "👾", "color": "#9333ea"},
    "user": {"label": "USER", "icon": "👤", "color": "#94a3b8"},
}

def safe_json_for_html(obj):
    s = json.dumps(obj, ensure_ascii=False)
    return s.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

# ================= БАЗА =================
class DB:
    def __init__(self):
        self.lock = threading.RLock()
        self._ensure(JSON_USERS, EXAMPLE_USERS)
        self._ensure(JSON_CHAT, [])
        self._ensure(JSON_CHAT_HVH, [])
        self._ensure(JSON_LOGS, [])
        self._ensure(JSON_PROMO, EXAMPLE_PROMOS)
        self._ensure(JSON_STATS, {"cases_opened": 0, "upgrades": 0})
        self._ensure(JSON_NEWS, DEFAULT_NEWS)

        self.users = self.load(JSON_USERS, EXAMPLE_USERS)
        self._migrate()
        self.chat   = self.load(JSON_CHAT,  [])
        self.chat_hvh = self.load(JSON_CHAT_HVH, [])
        self.logs   = self.load(JSON_LOGS,  [])
        self.promos = self.load(JSON_PROMO, {})
        self.stats  = self.load(JSON_STATS, {"cases_opened": 0, "upgrades": 0})
        self.news   = self.load(JSON_NEWS, DEFAULT_NEWS)

    def _ensure(self, path, default):
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, separators=(',', ':'))

    def _empty_stats(self):
        return {"cases_opened": 0, "spent": 0, "won_value": 0, "upgrades": 0,
                "upgrade_wins": 0, "sold": 0, "chat_msgs": 0}

    def _migrate(self):
        changed = False
        now = int(time.time())
        for login, u in self.users.items():
            for k, d in [("incoming", []), ("outgoing", []), ("inbox", []),
                         ("daily_last", 0), ("friends", []), ("inventory", []),
                         ("role", "user"), ("xp", 0), ("level", 1),
                         ("sponsor", 0), ("hvh", False)]:
                if k not in u: u[k] = d; changed = True
            if "stats" not in u: u["stats"] = self._empty_stats(); changed = True
            new_inv = []
            for it in u.get("inventory", []):
                if "expires_at" not in it: it["expires_at"] = now + SKIN_LIFETIME
                merged = False
                for n in new_inv:
                    if n["id"] == it["id"]:
                        n["count"] = n.get("count", 1) + 1
                        merged = True
                        break
                if not merged:
                    it["count"] = 1
                    new_inv.append(it)
            if new_inv != u.get("inventory", []):
                u["inventory"] = new_inv
                changed = True
            u["level"] = calc_level(u.get("xp", 0))
        if changed: self.save_users()

    def _hash(self, pwd): return hashlib.sha256(pwd.encode()).hexdigest()
    def load(self, path, default):
        try:
            with open(path, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return default

    def save_users(self):
        with self.lock:
            try:
                with open(JSON_USERS, "w", encoding="utf-8") as f:
                    json.dump(self.users, f, ensure_ascii=False, separators=(',', ':'))
            except Exception: pass
    def save_chat(self):
        with self.lock:
            try:
                with open(JSON_CHAT, "w", encoding="utf-8") as f:
                    json.dump(self.chat, f, ensure_ascii=False, separators=(',', ':'))
            except Exception: pass
    def save_chat_hvh(self):
        with self.lock:
            try:
                with open(JSON_CHAT_HVH, "w", encoding="utf-8") as f:
                    json.dump(self.chat_hvh, f, ensure_ascii=False, separators=(',', ':'))
            except Exception: pass
    def save_logs(self):
        with self.lock:
            try:
                with open(JSON_LOGS, "w", encoding="utf-8") as f:
                    json.dump(self.logs, f, ensure_ascii=False, separators=(',', ':'))
            except Exception: pass
    def save_promos(self):
        with self.lock:
            try:
                with open(JSON_PROMO, "w", encoding="utf-8") as f:
                    json.dump(self.promos, f, ensure_ascii=False, separators=(',', ':'))
            except Exception: pass
    def save_stats(self):
        with self.lock:
            try:
                with open(JSON_STATS, "w", encoding="utf-8") as f:
                    json.dump(self.stats, f, ensure_ascii=False, separators=(',', ':'))
            except Exception: pass

    def add_log(self, user, action, details=""):
        self.logs.append({"time": time.strftime("%H:%M:%S"), "user": user, "action": action, "details": details})
        if len(self.logs) > 200: self.logs = self.logs[-200:]
        self.save_logs()

    def purge_expired(self, login):
        if login not in self.users: return 0
        now = int(time.time())
        inv = self.users[login].get("inventory", [])
        kept, removed = [], 0
        for it in inv:
            if it.get("expires_at", now + SKIN_LIFETIME) > now:
                kept.append(it)
            else:
                removed += it.get("count", 1)
        if removed:
            self.users[login]["inventory"] = kept
            self.save_users()
        return removed

    def purge_all(self):
        total = 0
        for login in list(self.users.keys()): total += self.purge_expired(login)
        return total

    def register(self, login, password, hvh=False):
        with self.lock:
            if not login or len(login) < 3: return False, "Логин минимум 3 символа"
            if len(password) < 3: return False, "Пароль минимум 3 символа"
            if login in self.users: return False, "Логин занят"
            self.users[login] = {
                "password": self._hash(password), "role": "user", "balance": START_BALANCE,
                "xp": 0, "level": 1, "inventory": [], "friends": [], "incoming": [],
                "outgoing": [], "inbox": [], "muted": False, "banned": False,
                "daily_last": 0, "stats": self._empty_stats(), "sponsor": 0, "hvh": hvh
            }
            self.save_users()
            return True, "OK"

    def login(self, login, password):
        if login not in self.users: return False, "Пользователь не найден"
        if self.users[login]["password"] != self._hash(password): return False, "Неверный пароль"
        if self.users[login].get("banned"): return False, "Вы забанены"
        self.purge_expired(login)
        return True, self.users[login]

    def update_user(self, login, data):
        with self.lock:
            if login in self.users:
                self.users[login].update(data)
                self.save_users()

    def add_chat(self, msg, hvh=False):
        with self.lock:
            if hvh:
                self.chat_hvh.append(msg)
                if len(self.chat_hvh) > 100: self.chat_hvh = self.chat_hvh[-100:]
                self.save_chat_hvh()
            else:
                self.chat.append(msg)
                if len(self.chat) > 100: self.chat = self.chat[-100:]
                self.save_chat()

db = DB()

# ================= СКИНЫ (ТОЛЬКО ЧТЕНИЕ) =================
ALL_SKINS = []
CASES = {}
try:
    with open(JSON_SKINS, "r", encoding="utf-8") as f:
        data = json.load(f)
        ALL_SKINS = data.get("skins", [])
        CASES = data.get("cases", {})
    print(f"✅ Загружено {len(ALL_SKINS)} скинов и {len(CASES)} кейсов из {JSON_SKINS}")
except Exception as e:
    print(f"❌ Ошибка чтения {JSON_SKINS}: {e}")
    print("   Сервер запустится без скинов. Проверьте файл или запустите generator.py.")

SKINS_JSON_BYTES = json.dumps(ALL_SKINS, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
CASES_JSON_BYTES = json.dumps(CASES, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

ROLES_JSON = safe_json_for_html(ROLE_META)
VERSION_JSON = safe_json_for_html(CURRENT_VERSION)
VERSION_SUFFIX_JSON = safe_json_for_html(VERSION_SUFFIX)
SKIN_LIFETIME_JSON = safe_json_for_html(SKIN_LIFETIME)
LOADING_FACTS_JSON = safe_json_for_html(LOADING_FACTS)
XP_REWARDS_JSON = safe_json_for_html(XP_REWARDS)
START_BALANCE_JSON = safe_json_for_html(START_BALANCE)

# ================= HTML =================
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, viewport-fit=cover">
<meta name="theme-color" content="#0a0a14">
<title>CS:GO Drop v__VERSION__ __VERSION_SUFFIX__</title>
<style>
* { box-sizing: border-box; font-family: -apple-system, 'Segoe UI', Roboto, Tahoma, sans-serif; }
:root { --common:#b0c3d9; --uncommon:#8847ff; --rare:#d32ce6; --epic:#eb4b4b; --legendary:#ffd700; --bg:#0a0a14; --text:#f1f5f9; --panel:#151726; --header:#0f111d; --border:#2a2d3e; --accent:#a855f7; --gold:#ffd700; --tap:44px; }
html { -webkit-text-size-adjust:100%; }
body { background: radial-gradient(1200px 600px at 10% -10%, #1a0f30 0%, var(--bg) 60%); color:var(--text); margin:0; padding:0; display:flex; flex-direction:column; height:100vh; height:100dvh; overflow:hidden; -webkit-tap-highlight-color:transparent; touch-action:manipulation; padding-top:env(safe-area-inset-top); padding-bottom:env(safe-area-inset-bottom); }
body.theme-dark { }
body.theme-light { --bg:#f1f5f9; --text:#0f172a; --panel:#ffffff; --header:#e2e8f0; --border:#cbd5e1; --accent:#3b82f6; }
body.theme-neon { --bg:#050505; --text:#e0ffe0; --panel:#111111; --header:#1a1a1a; --border:#333333; --accent:#00ffcc; }
body.theme-cyber { --bg:#0d0221; --text:#00ffcc; --panel:#190a33; --header:#240e45; --border:#ff00ff; --accent:#ff00ff; }
body.theme-blood { --bg:#120000; --text:#ffcccc; --panel:#2b0000; --header:#3d0000; --border:#660000; --accent:#ff1a1a; }
body.theme-forest { --bg:#051205; --text:#ccffcc; --panel:#0a240a; --header:#0f330f; --border:#1e5e1e; --accent:#32cd32; }
body.theme-ocean { --bg:#001529; --text:#e0f7fa; --panel:#002b4d; --header:#003d6b; --border:#0077b6; --accent:#00b4d8; }
body.theme-matrix { --bg:#000; --text:#00ff00; --panel:#0a0a0a; --header:#111; --border:#003300; --accent:#00ff00; }
body.theme-gold { --bg:#121212; --text:#fff8dc; --panel:#1c1c1c; --header:#262626; --border:#b8860b; --accent:#ffd700; }

button, .case-card, .mult-btn, .nav-item, .inv-item, .skin-pick, .admin-btn, .btn-action, .btn-skip, .slot, .logout-btn, .news-btn, .modal-close, .result-item, .friend-item, .top-row { cursor:pointer; touch-action:manipulation; -webkit-touch-callout:none; -webkit-tap-highlight-color:transparent; }
button:active, .btn-action:active, .case-card:active, .inv-item:active, .mult-btn:active, .nav-item:active, .admin-btn:active, .skin-pick:active, .modal-close:active, .logout-btn:active, .news-btn:active, .btn-skip:active { filter:brightness(1.2); transition:filter .08s; }
input, select, textarea { font-size:16px !important; }
::-webkit-scrollbar { width:6px; height:6px; } ::-webkit-scrollbar-thumb { background:var(--accent); border-radius:3px; }

#boot-overlay { position:fixed; inset:0; background:#0a0a14; z-index:99999; display:flex; flex-direction:column; justify-content:center; align-items:center; padding:24px; text-align:center; font-size:14px; line-height:1.6; color:#fff; transition: opacity 0.4s; }
#boot-overlay.hidden { opacity: 0; pointer-events: none; }
.spinner { width:44px; height:44px; border:4px solid #2a2d3e; border-top-color:#a855f7; border-radius:50%; animation:spin 1s linear infinite; margin-bottom:16px; }
@keyframes spin { to { transform:rotate(360deg); } }
.boot-progress-wrap { width:100%; max-width:340px; margin:18px 0 14px; }
.boot-progress-bar { width:100%; height:10px; background:#2a2d3e; border-radius:8px; overflow:hidden; }
#boot-progress-fill { height:100%; width:0%; background:linear-gradient(90deg,#a855f7,#22c55e); border-radius:8px; transition:width .35s ease; }
.boot-progress-info { display:flex; justify-content:space-between; font-size:11px; margin-top:6px; opacity:.8; }
#boot-progress-pct { font-weight:900; color:#22c55e; }
.boot-fact-box { max-width:360px; background:#151726; border:1px solid #2a2d3e; border-left:4px solid #a855f7; border-radius:10px; padding:12px 14px; margin-top:6px; text-align:left; font-size:12px; line-height:1.5; }
.boot-fact-label { font-size:10px; font-weight:900; color:#f59e0b; letter-spacing:1px; margin-bottom:6px; }

#auth-screen { position:fixed; inset:0; background:var(--bg); z-index:9999; display:flex; justify-content:center; align-items:center; flex-direction:column; padding:20px; }
.auth-box { background:var(--panel); padding:24px; border-radius:12px; width:100%; max-width:380px; border:1px solid var(--border); box-shadow:0 10px 30px rgba(0,0,0,.5); }
.auth-box h2 { margin-top:0; text-align:center; color:var(--accent); }
.auth-box input { width:100%; padding:12px; margin-bottom:10px; background:var(--bg); border:1px solid var(--border); color:var(--text); border-radius:8px; }
.auth-box button { width:100%; padding:14px; min-height:var(--tap); background:var(--accent); color:#fff; border:none; border-radius:8px; font-weight:bold; cursor:pointer; margin-bottom:10px; }
.auth-box .switch-btn { background:transparent; color:var(--text); opacity:.7; }
.error-box { background:#ef4444; color:#fff; padding:10px; border-radius:8px; margin-bottom:10px; display:none; font-size:13px; }
.checkbox-row { display:flex; align-items:center; gap:8px; margin-bottom:10px; font-size:12px; color:#aaa; }
.checkbox-row input { width:auto; margin:0; }

.header { display:flex; justify-content:space-between; align-items:center; padding:10px 12px; background:var(--header); border-bottom:1px solid var(--border); z-index:10; gap:8px; flex-wrap:wrap; backdrop-filter: blur(10px); }
.logo { font-size:15px; font-weight:900; color:var(--accent); letter-spacing:1px; display:flex; align-items:center; gap:5px; flex-shrink:0; text-shadow: 0 0 10px rgba(168,85,247,.5); }
.logo::before { content:'🔫'; font-size:16px; }
.beta-badge { background:#f59e0b; color:#000; font-size:8px; padding:1px 5px; border-radius:4px; margin-left:4px; font-weight:900; letter-spacing:.5px; }
.user-info { display:flex; align-items:center; gap:6px; flex-wrap:wrap; flex:1; justify-content:flex-end; }
.role-badge { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:10px; font-size:10px; font-weight:900; color:#fff; }
.balance { background:var(--panel); padding:5px 12px; border-radius:20px; font-weight:bold; color:#22c55e; border:1px solid var(--border); font-size:13px; white-space:nowrap; }
.xp-info { background:var(--panel); padding:5px 10px; border-radius:20px; font-size:11px; border:1px solid var(--border); color:#eab308; white-space:nowrap; cursor:help; }
.logout-btn, .news-btn { border:none; padding:8px 10px; border-radius:6px; cursor:pointer; font-size:12px; min-height:36px; color:#fff; }
.logout-btn { background:#ef4444; }
.news-btn { background:#f59e0b; position:relative; }

.content { flex:1; overflow-y:auto; -webkit-overflow-scrolling:touch; padding:12px; padding-bottom:calc(80px + env(safe-area-inset-bottom)); overscroll-behavior:contain; }
.tab { display:none; }
.tab.active { display:block; }

.cases-grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin-bottom:12px; }
.case-card { background:var(--panel); border-radius:10px; padding:10px; text-align:center; border:2px solid var(--border); transition:.15s; min-height:70px; position:relative; overflow:hidden; }
.case-card.active { border-color:var(--accent); box-shadow:0 0 15px var(--accent); }
.case-card .case-glow { position:absolute; inset:0; background:radial-gradient(circle at center, rgba(168,85,247,0.1), transparent 70%); pointer-events:none; }

.roulette-container { position:relative; width:100%; height:96px; background:var(--panel); border-radius:10px; margin-bottom:12px; overflow:hidden; display:flex; align-items:center; border:2px solid var(--border); }
.arrow { position:absolute; top:0; left:50%; transform:translateX(-50%); width:0; height:0; border-left:8px solid transparent; border-right:8px solid transparent; border-top:12px solid var(--accent); z-index:10; }
.roulette-strip { display:flex; position:absolute; left:0; top:0; height:100%; align-items:center; will-change:transform; }

.item { width:76px; height:80px; margin:0 4px; border-radius:6px; display:flex; flex-direction:column; align-items:center; justify-content:center; font-size:8px; font-weight:bold; flex-shrink:0; text-align:center; padding:4px; border-width:3px; border-style:solid; background:var(--bg); }
.item img { width: 100%; height: 40px; object-fit: contain; margin-bottom:2px; }
.item-icon { font-size:24px; margin-bottom:3px; }
.rarity-common { border-color:var(--common); } .rarity-uncommon { border-color:var(--uncommon); } .rarity-rare { border-color:var(--rare); } .rarity-epic { border-color:var(--epic); } .rarity-legendary { border-color:var(--legendary); box-shadow:0 0 10px var(--legendary); }

.multiplier { display:flex; gap:5px; margin-bottom:10px; flex-wrap:wrap; }
.mult-btn { flex:1; min-width:var(--tap); min-height:var(--tap); background:var(--panel); color:var(--text); border:2px solid var(--border); border-radius:8px; font-weight:bold; font-size:13px; }
.mult-btn.active { border-color:var(--accent); color:var(--accent); }

.btn-action { width:100%; min-height:48px; padding:14px; background:var(--accent); color:#fff; border:none; border-radius:10px; font-weight:bold; font-size:15px; margin-bottom:6px; }
.btn-action:disabled { background:#444; color:#888; }
.btn-skip { background:transparent; border:2px solid var(--accent); color:var(--accent); padding:10px; width:100%; min-height:44px; border-radius:8px; font-weight:bold; margin-bottom:6px; }
.btn-sm { padding:6px 10px; font-size:12px; min-height:36px; border-radius:6px; }

.upgrade-container { position:relative; width:160px; height:160px; margin:0 auto 12px; display:flex; justify-content:center; align-items:center; }
.upgrade-svg { width:100%; height:100%; transform:rotate(-90deg); }
.upgrade-svg circle { fill:none; stroke-width:14; }
.upgrade-track { stroke:var(--border); }
.upgrade-target { stroke:#22c55e; stroke-linecap:butt; }
.upgrade-arrow-line { position:absolute; width:5px; height:56px; background:var(--text); bottom:50%; left:50%; transform-origin:bottom center; transform:translateX(-50%) rotate(0deg); border-radius:3px; z-index:5; }
.upgrade-arrow-line::before { content:''; position:absolute; top:-8px; left:-5px; border-left:7px solid transparent; border-right:7px solid transparent; border-bottom:11px solid var(--text); }
.upgrade-chance-text { position:absolute; font-size:28px; font-weight:900; z-index:2; top:50%; left:50%; transform:translate(-50%, -50%); pointer-events:none; }
.upgrade-slots { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; gap:8px; }
.slot { flex:1; max-width:120px; aspect-ratio:1; background:var(--panel); border-radius:10px; border:2px dashed var(--border); display:flex; flex-direction:column; justify-content:center; align-items:center; font-size:11px; margin:0 auto; position:relative; overflow:hidden; }
.slot.filled { border-style:solid; border-color:var(--accent); }
.slot img { max-width:80%; max-height:50%; object-fit:contain; }

.auto-btns { display:flex; gap:4px; margin-bottom:10px; flex-wrap:wrap; }
.auto-btns .mult-btn { flex:1; min-width:48px; min-height:40px; font-size:12px; padding:6px 2px; border-color:#22c55e; color:#22c55e; }

.inv-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(78px, 1fr)); gap:6px; }
.inv-item { background:var(--panel); border-radius:8px; padding:5px; text-align:center; border:2px solid var(--border); position:relative; min-height:100px; display:flex; flex-direction:column; align-items:center; justify-content:center; }
.inv-item.selected { border-color:#22c55e; background:#22c55e22; }
.inv-item img { width: 60px; height: 45px; object-fit: contain; margin-bottom:2px; }
.inv-item .count-badge { position:absolute; top:2px; left:2px; background:var(--accent); color:#fff; font-size:9px; padding:1px 5px; border-radius:8px; font-weight:900; }
.inv-item .skin-timer { position:absolute; top:2px; right:2px; background:#ef4444; color:#fff; font-size:7px; padding:1px 4px; border-radius:4px; font-weight:900; font-family:monospace; }
.inv-item .skin-timer.long { background:#22c55e; }

.chat-container { display:flex; flex-direction:column; height:100%; }
.chat-messages { background:var(--panel); border-radius:10px; padding:10px; margin-bottom:10px; border:1px solid var(--border); flex:1; min-height:300px; max-height:55vh; overflow-y:auto; -webkit-overflow-scrolling:touch; }
.chat-msg { margin-bottom:8px; font-size:13px; line-height:1.4; }
.chat-msg .badge-role { display:inline-block; padding:1px 6px; border-radius:8px; font-size:9px; font-weight:900; color:#fff; margin-right:4px; }
.chat-msg b { color:var(--accent); }
.chat-msg.muted b { color:#ef4444; text-decoration:line-through; }
.chat-input-area { display:flex; gap:8px; position:sticky; bottom:0; background:var(--bg); padding-top:6px; }
.chat-input-area input { flex:1; padding:12px; border-radius:8px; border:1px solid var(--border); background:var(--bg); color:var(--text); }
.chat-input-area button { width:auto; min-width:50px; padding:12px; margin:0; }

.admin-section { background:var(--panel); padding:12px; border-radius:10px; margin-bottom:12px; border:1px solid var(--border); }
.admin-section h3 { margin-top:0; color:var(--accent); font-size:15px; }
.admin-input, select { width:100%; padding:10px; margin-bottom:6px; background:var(--bg); border:1px solid var(--border); color:var(--text); border-radius:6px; }
.admin-btn { background:var(--accent); color:#fff; border:none; padding:10px 14px; border-radius:6px; font-weight:bold; margin-right:4px; margin-bottom:4px; min-height:40px; }
.admin-btn.danger { background:#ef4444; } .admin-btn.success { background:#22c55e; } .admin-btn.warn { background:#f59e0b; }

.row { display:flex; gap:6px; align-items:center; margin-bottom:6px; flex-wrap:wrap; }
.row > * { flex:1 1 120px; min-width:0; }
.row .fixed { flex:0 0 auto; width:auto; }

.profile-card { background:var(--panel); padding:16px; border-radius:12px; text-align:center; border:1px solid var(--border); margin-bottom:12px; }
.profile-avatar { font-size:50px; margin-bottom:8px; }
.profile-name { font-size:20px; font-weight:bold; color:var(--accent); margin-bottom:8px; }
.profile-stats { font-size:13px; opacity:.9; margin-bottom:12px; line-height:1.6; }

.friend-item { display:flex; justify-content:space-between; align-items:center; padding:8px; border-bottom:1px solid var(--border); gap:5px; flex-wrap:wrap; }
.online-dot { display:inline-block; width:8px; height:8px; border-radius:50%; background:#22c55e; margin-right:5px; }

.nav-bar { position:fixed; bottom:0; left:0; right:0; background:var(--header); border-top:1px solid var(--border); display:flex; justify-content:flex-start; align-items:center; padding:4px 4px calc(4px + env(safe-area-inset-bottom)); z-index:50; overflow-x:auto; -webkit-overflow-scrolling:touch; gap:2px; scrollbar-width:none; backdrop-filter: blur(10px); }
.nav-bar::-webkit-scrollbar { display:none; }
.nav-item { display:flex; flex-direction:column; align-items:center; justify-content:center; font-size:9px; color:var(--text); opacity:.55; min-width:56px; padding:6px 4px; position:relative; flex:0 0 auto; border-radius:8px; }
.nav-item.active { opacity:1; color:var(--accent); background:var(--panel); }
.nav-item .nav-icon { font-size:20px; margin-bottom:2px; }
.nav-item .nav-badge { position:absolute; top:2px; right:6px; background:#ef4444; color:#fff; font-size:8px; padding:0 4px; border-radius:8px; font-weight:900; }

.result-modal, .confirm-modal, .news-modal, .modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,.85); z-index:1000; justify-content:center; align-items:center; padding:16px; padding-top:max(16px, env(safe-area-inset-top)); padding-bottom:max(16px, env(safe-area-inset-bottom)); backdrop-filter: blur(5px); }
.result-modal.active, .confirm-modal.active, .news-modal.active { display:flex; }
.modal.active { display:block; overflow-y:auto; }

.result-box, .confirm-box, .news-box { background:var(--panel); border-radius:12px; padding:18px; width:100%; max-width:380px; text-align:center; border:2px solid var(--accent); max-height:90vh; overflow-y:auto; -webkit-overflow-scrolling:touch; box-shadow:0 0 30px rgba(168,85,247,0.3); }
.result-items { display:flex; flex-direction:column; gap:6px; margin:10px 0; max-height:50vh; overflow-y:auto; }
.result-item { background:var(--bg); padding:8px; border-radius:8px; display:flex; align-items:center; justify-content:space-between; }
.result-item img { width: 40px; height: 30px; object-fit: contain; }
.result-item .name { font-size:12px; font-weight:bold; flex:1; text-align:left; margin-left:10px; }
.result-item .price { color:#22c55e; font-size:12px; font-weight:bold; }

.modal-close { position:fixed; top:max(16px, env(safe-area-inset-top)); right:16px; font-size:22px; color:var(--text); background:var(--panel); width:44px; height:44px; display:flex; justify-content:center; align-items:center; border-radius:50%; border:1px solid var(--border); z-index:101; cursor:pointer; }

.skins-picker { display:grid; grid-template-columns:repeat(auto-fill, minmax(78px, 1fr)); gap:6px; margin-top:10px; max-height:50vh; overflow-y:auto; -webkit-overflow-scrolling:touch; }
.skin-pick { background:var(--panel); border-radius:8px; padding:5px; text-align:center; border:2px solid var(--border); font-size:9px; min-height:80px; }
.skin-pick.selected { border-color:#22c55e; }
.skin-pick img { width: 50px; height: 35px; object-fit: contain; }

#toast-container { position:fixed; top:calc(60px + env(safe-area-inset-top)); left:50%; transform:translateX(-50%); z-index:2000; display:flex; flex-direction:column; gap:8px; pointer-events:none; width:calc(100% - 24px); max-width:340px; }
.toast { background:var(--panel); color:var(--text); padding:12px 14px; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,.5); font-weight:bold; font-size:13px; border-left:4px solid var(--accent); opacity:0; transform:translateY(-20px); transition:all .3s; display:flex; align-items:center; gap:10px; }
.toast.show { opacity:1; transform:translateY(0); }
.toast.error { border-left-color:#ef4444; } .toast.success { border-left-color:#22c55e; } .toast.warn { border-left-color:#f59e0b; } .toast.xp { border-left-color:#eab308; }

.news-card { background:var(--bg); padding:12px; border-radius:8px; margin-bottom:10px; border-left:4px solid var(--accent); text-align:left; position:relative; }
.news-card .news-badge { position:absolute; top:8px; right:8px; background:#ef4444; color:#fff; font-size:9px; padding:2px 6px; border-radius:8px; font-weight:900; }
.news-card h4 { margin:0 0 4px 0; font-size:14px; padding-right:50px; }
.news-card .news-date { font-size:10px; opacity:.6; margin-bottom:6px; }
.news-card p { margin:0; font-size:12px; line-height:1.5; opacity:.9; }

.badge { display:inline-block; padding:2px 6px; border-radius:10px; background:var(--panel); border:1px solid var(--border); font-size:10px; margin:2px; }
.badge.unlocked { border-color:#22c55e; color:#22c55e; } .badge.locked { opacity:.4; }

.top-row { display:flex; justify-content:space-between; padding:8px; border-bottom:1px solid var(--border); font-size:13px; gap:8px; }
.top-rank { font-weight:bold; width:24px; color:var(--accent); flex-shrink:0; }

.promo-card { background:var(--bg); padding:10px; border-radius:8px; border:1px solid var(--border); margin-bottom:8px; font-size:12px; display:flex; justify-content:space-between; align-items:center; gap:6px; flex-wrap:wrap; }
.history-item { background:var(--panel); padding:6px; border-radius:6px; font-size:11px; margin-bottom:4px; display:flex; justify-content:space-between; gap:6px; }
.history-item img { width: 30px; height: 20px; object-fit: contain; }
.search-bar { width:100%; padding:10px; border-radius:8px; background:var(--bg); border:1px solid var(--border); color:var(--text); margin-bottom:10px; }
.log-item { font-size:12px; padding:6px; border-bottom:1px solid var(--border); font-family:ui-monospace, monospace; word-break:break-word; }
.log-item .time { color:#888; font-size:10px; margin-right:5px; }
.log-item .user { color:var(--accent); font-weight:bold; margin-right:5px; }
.log-item .action { color:#f59e0b; margin-right:5px; }

.coming-soon { text-align:center; padding:40px 20px; color:#888; }
.coming-soon .icon { font-size:60px; margin-bottom:15px; opacity:0.5; }
.coming-soon h3 { color:var(--accent); font-size:20px; margin:0 0 10px 0; }
.coming-soon p { font-size:13px; margin:0; }

@media (max-width:768px) { .content { padding:10px; padding-bottom:calc(84px + env(safe-area-inset-bottom)); } .inv-grid { grid-template-columns:repeat(auto-fill, minmax(76px, 1fr)); } .top-row { font-size:12px; padding:7px; } }
@media (max-width:520px) { .header { padding:8px 10px; gap:6px; } .logo { font-size:13px; } .role-badge { font-size:9px; padding:1px 5px; } .balance { font-size:12px; padding:4px 10px; } .logout-btn, .news-btn { padding:6px 8px; font-size:11px; min-height:34px; } .cases-grid { grid-template-columns:repeat(2, 1fr); gap:6px; } .case-card { padding:8px; min-height:62px; } .roulette-container { height:88px; } .item { width:68px; height:72px; font-size:7px; margin:0 3px; } .item img { height: 32px; } .item-icon { font-size:20px; } .upgrade-container { width:140px; height:140px; } .upgrade-chance-text { font-size:24px; } .slot { max-width:105px; font-size:10px; } .inv-grid { grid-template-columns:repeat(auto-fill, minmax(72px, 1fr)); gap:5px; } .skins-picker { grid-template-columns:repeat(auto-fill, minmax(72px, 1fr)); } .nav-item { min-width:52px; font-size:8px; padding:5px 3px; } .nav-item .nav-icon { font-size:18px; } .admin-section { padding:10px; } .row > * { flex:1 1 100%; } .row .fixed { flex:0 0 auto; width:100%; } .admin-btn { width:100%; margin-right:0; } .chat-messages { min-height:260px; max-height:50vh; } .friend-item > div { display:flex; gap:4px; flex-wrap:wrap; } .modal-close { width:40px; height:40px; font-size:20px; } .boot-fact-box { font-size:11px; padding:10px 12px; } .boot-progress-wrap { max-width:300px; } .inv-item img { width: 50px; height: 35px; } }
@media (max-width:380px) { .content { padding:8px; padding-bottom:calc(80px + env(safe-area-inset-bottom)); } .cases-grid { gap:5px; } .case-card { padding:6px; min-height:56px; } .inv-grid { grid-template-columns:repeat(3, 1fr); gap:4px; } .inv-item { min-height:86px; padding:3px; } .inv-item img { width: 45px; height: 30px; } .upgrade-container { width:120px; height:120px; } .upgrade-chance-text { font-size:20px; } .nav-item { min-width:46px; font-size:7px; } .nav-item .nav-icon { font-size:16px; } .mult-btn { min-width:38px; min-height:38px; font-size:11px; } .btn-action { min-height:44px; font-size:14px; } }
@media (max-height:500px) and (orientation:landscape) { .nav-bar { padding:2px 4px; } .nav-item { padding:3px 4px; } .nav-item .nav-icon { font-size:16px; margin-bottom:0; } .nav-item > div:last-child { display:none; } .chat-messages { max-height:40vh; } }
@media (hover:none) { .case-card:hover, .inv-item:hover, .mult-btn:hover, .skin-pick:hover, .nav-item:hover, .admin-btn:hover, .btn-action:hover { filter:none; } }
@media (prefers-reduced-motion:reduce) { *, *::before, *::after { animation-duration:0.01ms !important; transition-duration:0.01ms !important; } }
</style>
</head>
<body>

<div id="boot-overlay">
  <div class="spinner"></div>
  <div id="boot-text">Загрузка данных...</div>
  <div class="boot-progress-wrap">
    <div class="boot-progress-bar"><div id="boot-progress-fill"></div></div>
    <div class="boot-progress-info">
      <span id="boot-progress-pct">0%</span>
      <span id="boot-progress-stage">Инициализация...</span>
    </div>
  </div>
  <div class="boot-fact-box">
    <div class="boot-fact-label">💡 А ты знал?</div>
    <div id="boot-fact">Загружаю прикольные факты...</div>
  </div>
  <div id="boot-err" class="err" style="display:none;"></div>
  <button id="boot-retry" style="display:none;" onclick="location.reload()">Перезагрузить</button>
</div>

<datalist id="all-users-list"></datalist>
<datalist id="all-users-list-2"></datalist>

<div id="auth-screen" style="display:none;">
  <div class="auth-box">
    <h2 id="auth-title">Вход</h2>
    <div id="auth-error" class="error-box"></div>
    <input type="text" id="auth-login" placeholder="Логин" autocomplete="username" autocapitalize="off" autocorrect="off" spellcheck="false">
    <input type="password" id="auth-password" placeholder="Пароль" autocomplete="current-password">
    <div class="checkbox-row"><input type="checkbox" id="auth-hvh"><label for="auth-hvh">HvH аккаунт (разрешены баги)</label></div>
    <button onclick="submitAuth()" id="auth-submit">Войти</button>
    <button class="switch-btn" onclick="toggleAuthMode()" id="auth-switch">Нет аккаунта? Регистрация</button>
  </div>
</div>

<div id="main-app" style="display:none; flex-direction:column; height:100vh; height:100dvh;">
  <div id="toast-container"></div>
  <div id="result-modal" class="result-modal"><div class="result-box"><h2 id="res-title">Результат</h2><div class="result-items" id="res-items"></div><button class="btn-action" onclick="closeResult()">Забрать</button></div></div>
  <div id="confirm-modal" class="confirm-modal"><div class="confirm-box"><h3 id="confirm-title">Подтверждение</h3><p id="confirm-text" style="font-size:14px; opacity:0.8;">Вы уверены?</p><div style="display:flex; gap:8px; margin-top:10px;"><button class="btn-action" style="background:#ef4444;" onclick="confirmYes()">Да</button><button class="btn-action" style="background:#666;" onclick="confirmNo()">Отмена</button></div></div></div>
  <div id="news-modal" class="news-modal"><div class="news-box"><h2>📰 Что нового (v<span id="news-ver"></span>)</h2><div id="news-modal-list" style="text-align:left;"></div><button class="btn-action" onclick="closeNewsModal()" style="margin-top:15px;">Понятно</button></div></div>

  <div class="header">
    <div class="logo">CS:GO DROP<span class="beta-badge">BETA</span></div>
    <div class="user-info">
      <span id="role-badge-header" class="role-badge" style="display:none;"></span>
      <span style="font-size:12px; opacity:0.7;" id="username-display"></span>
      <div class="balance" id="balance-display">0</div>
      <div class="xp-info" id="xp-display" title="Уровень и XP">Lvl 1 · 0 XP</div>
      <button class="news-btn" onclick="toggleSound()" title="Звук">🔊</button>
      <button class="news-btn" onclick="dailyRoulette()" title="Рулетка дня">🎡</button>
      <button class="news-btn" onclick="openNewsModal()">📰<span id="news-badge" class="nav-badge" style="display:none;"></span></button>
      <button class="logout-btn" onclick="logout()">Выход</button>
    </div>
  </div>

  <div class="content">
    <div id="tab-cases" class="tab active">
      <div class="cases-grid" id="cases-container"></div>
      <div class="roulette-container" id="roulette-container"><div class="arrow"></div><div class="roulette-strip" id="strip"></div></div>
      <div class="multiplier"><button class="mult-btn active" onclick="setMult(1, this)">x1</button><button class="mult-btn" onclick="setMult(2, this)">x2</button><button class="mult-btn" onclick="setMult(3, this)">x3</button><button class="mult-btn" onclick="setMult(5, this)">x5</button><button class="mult-btn" onclick="setMult(10, this)">x10</button></div>
      <button class="btn-action" id="btn-open-case" onclick="openCase()">Открыть</button>
      <button class="btn-skip" onclick="skipAnimation()" id="btn-skip" style="display:none;">⏩ Пропустить анимацию</button>
      <h3 style="margin-top:20px;">📜 История дропов</h3><div id="drop-history" style="max-height:200px; overflow-y:auto;"></div>
    </div>

    <div id="tab-upgrade" class="tab">
      <h2 style="text-align:center; margin-bottom: 10px;">Апгрейд скинов</h2>
      <p style="text-align:center; font-size:11px; opacity:0.7; margin-bottom: 15px;">Комиссия: <span id="upg-fee" style="color:#ef4444; font-weight:bold;">$0</span></p>
      <div class="upgrade-container"><svg class="upgrade-svg" viewBox="0 0 200 200"><circle class="upgrade-track" cx="100" cy="100" r="85"></circle><circle class="upgrade-target" id="upg-target-circle" cx="100" cy="100" r="85"></circle></svg><div class="upgrade-arrow-line" id="upg-arrow"></div><div class="upgrade-chance-text" id="upg-chance">0%</div></div>
      <div class="upgrade-slots"><div class="slot" id="slot-left" onclick="openModal('left')"><div style="font-size:30px; margin-bottom:5px;">➕</div><div>Твой предмет</div></div><div style="font-size: 20px; opacity: 0.5;">➔</div><div class="slot" id="slot-right" onclick="openModal('right')"><div style="font-size:30px; margin-bottom:5px;">🎯</div><div>Цель</div></div></div>
      <h3 style="font-size:14px; text-align:center; margin-bottom:8px;">⚡ Авто-выбор цели</h3>
      <div class="auto-btns"><button class="mult-btn" onclick="autoPickTarget(1.5)">×1.5</button><button class="mult-btn" onclick="autoPickTarget(2)">×2</button><button class="mult-btn" onclick="autoPickTarget(3)">×3</button><button class="mult-btn" onclick="autoPickTarget(10)">×10</button><button class="mult-btn" onclick="autoPickTarget(100)">×100</button></div>
      <button class="btn-action" id="btn-upgrade-start" onclick="startUpgrade()" disabled>Крутить</button>
    </div>

    <div id="tab-inventory" class="tab">
      <h2>Инвентарь</h2>
      <div style="display:flex; justify-content:space-between; margin-bottom:10px; font-size:13px; gap:8px;"><span>Скинов: <b id="inv-count">0</b></span><span>Стоимость: <b style="color:#22c55e;">$<span id="inv-total">0</span></b></span></div>
      <div style="background:#ef444422; padding:8px; border-radius:8px; font-size:11px; margin-bottom:10px; border-left:3px solid #ef4444;">⏳ <b>Внимание:</b> каждый скин удаляется через 24 часа. Зайди во вкладку <b>⏳ Жизнь скинов</b>, чтобы продлить их.</div>
      <input class="search-bar" id="inv-search" placeholder="🔍 Поиск по названию..." oninput="renderInventory()">
      <div class="row" style="margin-bottom:10px;"><select id="inv-sort" onchange="renderInventory()"><option value="expires">Скоро истекает</option><option value="price_desc">Цена ↓</option><option value="price_asc">Цена ↑</option><option value="rarity">Редкость</option><option value="name">Имя</option></select><button class="btn-action btn-sm fixed" style="background:#22c55e;" onclick="sellAll()">Продать всё</button></div>
      <div class="row" style="margin-bottom:10px;"><button class="admin-btn btn-sm fixed" onclick="selectAllInv()">Выбрать все</button><button class="admin-btn btn-sm fixed success" onclick="sellSelected()">Продать выбранные</button><button class="admin-btn btn-sm fixed" onclick="clearSelection()">Сбросить выбор</button></div>
      <div class="inv-grid" id="inventory-grid"></div>
    </div>

    <div id="tab-lifetime" class="tab">
      <h2>⏳ Жизнь скинов</h2>
      <div style="background:var(--panel); padding:10px; border-radius:8px; margin-bottom:12px; font-size:12px; border-left:3px solid #3b82f6;">
        Каждый скин можно продлить на <b>1</b>, <b>7</b>, <b>30</b> или <b>100 дней</b>. Стоимость = <b style="color:#22c55e;">1.3× от цены скина</b> за 1 день.
      </div>
      
      <div class="row" style="margin-bottom:10px;">
        <button class="admin-btn fixed" onclick="selectLifetimeDays(1)">1 день</button>
        <button class="admin-btn fixed" onclick="selectLifetimeDays(7)">7 дней</button>
        <button class="admin-btn fixed" onclick="selectLifetimeDays(30)">30 дней</button>
        <button class="admin-btn fixed" onclick="selectLifetimeDays(100)">100 дней</button>
      </div>
      
      <div style="background:var(--panel); padding:10px; border-radius:8px; margin-bottom:12px; text-align:center;">
        <div style="font-size:12px; opacity:0.7; margin-bottom:4px;">Выбрано дней:</div>
        <div style="font-size:24px; font-weight:900; color:var(--accent);" id="lifetime-selected">1</div>
      </div>
      
      <div class="row" style="margin-bottom:10px;">
        <button class="admin-btn fixed" onclick="extendAllWithDays()">Продлить все</button>
        <button class="admin-btn danger fixed" onclick="extendSelectedWithDays()">Продлить выбранные</button>
      </div>
      
      <h3 style="font-size:14px;">📋 Твои скины:</h3>
      <div id="lifetime-grid" class="inv-grid"></div>
    </div>

    <div id="tab-shop" class="tab">
      <h2>🛒 Магазин</h2>
      <div id="daily-discount" style="background:#f59e0b22; padding:8px; border-radius:8px; font-size:11px; margin-bottom:10px; border-left:3px solid #f59e0b;"></div>
      <input class="search-bar" id="shop-search" placeholder="🔍 Поиск..." oninput="resetShopPage(); renderShop()">
      <select id="shop-rarity" onchange="resetShopPage(); renderShop()" style="margin-bottom:10px;"><option value="">Все редкости</option><option value="common">Common</option><option value="uncommon">Uncommon</option><option value="rare">Rare</option><option value="epic">Epic</option><option value="legendary">Legendary</option></select>
      <div class="inv-grid" id="shop-grid"></div>
      <div id="shop-pagination" class="row" style="margin-top:10px; justify-content:center; display:none;">
        <button class="admin-btn fixed" onclick="changeShopPage(-1)">← Назад</button>
        <span id="shop-page-info" style="padding:10px; font-size:13px;">1 / 1</span>
        <button class="admin-btn fixed" onclick="changeShopPage(1)">Вперёд →</button>
      </div>
    </div>

    <div id="tab-chat" class="tab"><h2>💬 Общий чат</h2><div class="chat-container"><div class="chat-messages" id="chat-messages"></div><div class="chat-input-area" id="chat-input-area"><input type="text" id="chat-input" placeholder="Написать сообщение..." autocomplete="off" onkeypress="if(event.key==='Enter')sendChat()"><button class="btn-action" onclick="sendChat()">→</button></div><div id="chat-muted-msg" style="display:none; color:#ef4444; font-weight:bold; text-align:center; margin-top:10px;">Вы заглушены (мут).</div></div></div>

    <div id="tab-news" class="tab"><h2>📰 Новости обновлений</h2><div id="news-list"></div></div>
    
    <div id="tab-craft" class="tab">
      <div class="coming-soon">
        <div class="icon">🔨</div>
        <h3>Крафты скоро!</h3>
        <p>Мы работаем над новой системой крафта. Совсем скоро ты сможешь собирать эксклюзивные скины из обычных.</p>
      </div>
    </div>

    <div id="tab-rules" class="tab">
      <h2>📜 Правила</h2>
      <div class="admin-section">
        <h3>1. Общие положения</h3>
        <p style="font-size:12px; line-height:1.6; opacity:.9;">Запрещено использовать читы, баги и эксплойты, кроме HvH-аккаунтов. Оскорбление администрации и других игроков карается баном.</p>
        <h3>2. Экономика</h3>
        <p style="font-size:12px; line-height:1.6; opacity:.9;">Использование багов для получения валюты запрещено. Запрещено продавать игровую валюту за реальные деньги.</p>
        <h3>3. Спонсоры</h3>
        <p style="font-size:12px; line-height:1.6; opacity:.9;">Спонсоры — это VIP-уровни. 1-10 уровней. Отображается в профиле и чате. Выдаётся администрацией.</p>
        <h3>4. HvH</h3>
        <p style="font-size:12px; line-height:1.6; opacity:.9;">HvH-аккаунты — отдельный тип. Они имеют свой чат и лидерборд. Использование багов разрешено только в HvH-режиме.</p>
      </div>
    </div>

    <div id="tab-profile" class="tab">
      <h2>Профиль</h2>
      <div class="profile-card">
        <div class="profile-avatar">👤</div>
        <div class="profile-name" id="prof-name">-</div>
        <div id="prof-role-rank" style="margin-bottom:10px;"></div>
        <div class="profile-stats">Баланс: <b style="color:#22c55e;">$<span id="prof-balance">0</span></b><br>Скинов: <b id="prof-inv-count">0</b> · Стоимость: <b style="color:#22c55e;">$<span id="prof-inv-value">0</span></b></div>
        <div id="prof-xp" style="font-size:13px; margin-bottom:10px; color:#eab308;"></div>
        <button class="btn-action btn-sm" style="background:#f59e0b;" onclick="claimDaily()" id="btn-daily">🎁 Ежедневный бонус</button>
      </div>
      
      <div class="admin-section"><h3>📥 Входящие сообщения (<span id="inbox-count">0</span>)</h3><div id="inbox-list" style="max-height:200px; overflow-y:auto;"></div></div>

      <div class="admin-section"><h3>📊 Статистика</h3><div id="prof-stats" style="font-size:12px; line-height:1.8;"></div></div>
      <div class="admin-section"><h3>🏆 Достижения</h3><div id="achievements"></div></div>
      <div class="admin-section"><h3>🎟️ Промокод</h3><div class="row"><input class="admin-input" id="promo-input" placeholder="Введите код" style="margin-bottom:0;"><button class="admin-btn fixed" onclick="redeemPromo()">Активировать</button></div></div>
      <div class="admin-section"><h3>👥 Добавить друга</h3><div class="row"><input class="admin-input" id="friend-input" placeholder="Логин друга" style="margin-bottom:0;" list="all-users-list"><button class="admin-btn fixed" onclick="sendFriendRequest()">Отправить заявку</button></div></div>
      <div class="admin-section"><h3>📥 Входящие заявки (<span id="incoming-count">0</span>)</h3><div id="incoming-list"></div></div>
      <div class="admin-section"><h3>📤 Исходящие (<span id="outgoing-count">0</span>)</h3><div id="outgoing-list"></div></div>
      <div class="admin-section"><h3>👫 Мои друзья (<span id="friends-count">0</span>)</h3><div id="friends-list"></div></div>
      <div class="admin-section" style="border-color:#ef4444;"><h3 style="color:#ef4444;">⚠️ Опасная зона</h3><button class="admin-btn danger" onclick="deleteAccount()">Удалить аккаунт навсегда</button></div>
    </div>

    <div id="tab-top" class="tab">
      <h2>🏆 Топ игроков</h2>
      <div class="admin-section"><h3>💰 По балансу</h3><div id="top-balance"></div></div>
      <div class="admin-section"><h3>🎒 По стоимости инвентаря</h3><div id="top-inventory"></div></div>
      <div class="admin-section"><h3>⭐ По уровню</h3><div id="top-level"></div></div>
    </div>

    <div id="tab-admin" class="tab">
      <h2 style="color:#ef4444;">⚙️ Админ-панель</h2>
      
      <div id="admin-only-admin" style="display:none;">
        <div class="admin-section"><h3>💰 Выдать деньги</h3><div class="row"><input class="admin-input" id="give-money-user" placeholder="Логин" list="all-users-list-2" style="margin-bottom:0;"><input class="admin-input" id="give-money-amount" type="number" placeholder="Сумма" style="margin-bottom:0;"><button class="admin-btn success fixed" onclick="adminAction('give_money')">Выдать</button></div></div>
        <div class="admin-section"><h3>💸 Забрать деньги</h3><div class="row"><input class="admin-input" id="take-money-user" placeholder="Логин" list="all-users-list-2" style="margin-bottom:0;"><input class="admin-input" id="take-money-amount" type="number" placeholder="Сумма" style="margin-bottom:0;"><button class="admin-btn danger fixed" onclick="adminAction('take_money')">Забрать</button></div></div>
        <div class="admin-section"><h3>🎁 Выдать скин</h3><input class="admin-input" id="give-skin-user" placeholder="Логин пользователя" list="all-users-list-2"><div class="row"><input class="admin-input" id="skin-search" placeholder="🔍 Поиск по названию..." oninput="renderSkinPicker()" style="margin-bottom:0;"><select id="skin-rarity-filter" class="fixed" style="width:auto; margin-bottom:0;" onchange="renderSkinPicker()"><option value="">Все</option><option value="common">Common</option><option value="uncommon">Uncommon</option><option value="rare">Rare</option><option value="epic">Epic</option><option value="legendary">Legendary</option></select></div><div id="skin-preview" style="margin:10px 0; padding:8px; background:var(--bg); border-radius:8px; font-size:12px;">Скин не выбран</div><div class="skins-picker" id="skin-picker-grid"></div><div class="row" style="margin-top:10px;"><button class="admin-btn success" onclick="adminAction('give_skin', 1)">Выдать ×1</button><button class="admin-btn success" onclick="adminAction('give_skin', 5)">×5</button><button class="admin-btn success" onclick="adminAction('give_skin', 10)">×10</button></div></div>
        <div class="admin-section"><h3>👑 Выдать роль</h3><input class="admin-input" id="role-user" placeholder="Логин" list="all-users-list-2"><div class="row"><button class="admin-btn" style="background:#ef4444;" onclick="adminAction('set_role', 'admin')">ADMIN</button><button class="admin-btn" style="background:#22d3ee;" onclick="adminAction('set_role', 'betatester')">BETA</button><button class="admin-btn" style="background:#f59e0b;" onclick="adminAction('set_role', 'gl_moder')">GL MODER</button><button class="admin-btn" style="background:#eab308;" onclick="adminAction('set_role', 'moder')">MODER</button><button class="admin-btn" style="background:#64748b;" onclick="adminAction('set_role', 'user')">USER</button></div></div>
        <div class="admin-section"><h3>⭐ Спонсор (уровень)</h3><input class="admin-input" id="sponsor-user" placeholder="Логин" list="all-users-list-2"><div class="row"><button class="admin-btn" style="background:#a855f7;" onclick="adminAction('set_sponsor', 1)">1</button><button class="admin-btn" style="background:#a855f7;" onclick="adminAction('set_sponsor', 3)">3</button><button class="admin-btn" style="background:#a855f7;" onclick="adminAction('set_sponsor', 5)">5</button><button class="admin-btn" style="background:#a855f7;" onclick="adminAction('set_sponsor', 10)">10</button><button class="admin-btn danger" onclick="adminAction('set_sponsor', 0)">Убрать</button></div></div>
        <div class="admin-section"><h3>🎟️ Промокоды</h3><div class="row"><input class="admin-input" id="promo-code" placeholder="CODE" style="margin-bottom:0;"><input class="admin-input" id="promo-amount" type="number" placeholder="Сумма" style="margin-bottom:0;"><input class="admin-input" id="promo-limit" type="number" placeholder="Лимит" style="margin-bottom:0;"><button class="admin-btn fixed" onclick="adminAction('create_promo')">Создать</button></div><div id="promo-list" style="margin-top:10px;"></div></div>
        <div class="admin-section"><h3>📢 Рассылка (в ЛС)</h3><button class="admin-btn warn" onclick="broadcast()">Отправить всем</button></div>
      </div>

      <div class="admin-section"><h3>🚫 Бан / Мут</h3><input class="admin-input" id="punish-user" placeholder="Логин" list="all-users-list-2"><div class="row"><button class="admin-btn danger" onclick="adminAction('ban')">Бан</button><button class="admin-btn warn" onclick="adminAction('mute')">Мут</button><button class="admin-btn success" onclick="adminAction('unban')">Разбан</button></div></div>
      
      <div id="admin-only-admin-2" style="display:none;">
        <div class="admin-section"><h3>🧹 Опасные действия</h3><input class="admin-input" id="danger-user" placeholder="Логин" list="all-users-list-2"><div class="row"><button class="admin-btn danger" onclick="adminAction('reset_inventory')">Сброс инвентаря</button><button class="admin-btn danger" onclick="adminAction('delete_user')">Удалить юзера</button></div></div>
        <div class="admin-section"><h3>📊 Все пользователи</h3><div id="admin-users" style="max-height:300px; overflow-y:auto;"></div></div>
        <div class="admin-section"><h3>🌐 Статистика сервера</h3><div id="server-stats" style="font-size:13px; line-height:1.7;"></div></div>
      </div>

      <div class="admin-section"><h3>🛠 Общие действия</h3><button class="admin-btn warn" style="margin-top:6px;" onclick="adminAction('clear_chat')">Очистить чат</button><button class="admin-btn warn" style="margin-top:6px;" onclick="adminAction('purge_expired')">Удалить просроченные скины</button></div>
      <div class="admin-section"><h3>📜 Логи (200)</h3><div id="admin-logs" style="max-height:400px; overflow-y:auto;"></div></div>
    </div>
  </div>

  <div class="modal" id="modal-select"><div class="modal-close" onclick="closeModal()">✖</div><h2 id="modal-title">Выбери предмет</h2><div class="inv-grid" id="modal-grid" style="margin-top: 20px;"></div></div>

  <div class="nav-bar" id="nav-bar">
    <div class="nav-item active" onclick="switchTab('cases', this)"><div class="nav-icon">📦</div><div>Кейсы</div></div>
    <div class="nav-item" onclick="switchTab('upgrade', this)"><div class="nav-icon">⬆️</div><div>Апгрейд</div></div>
    <div class="nav-item" onclick="switchTab('inventory', this)"><div class="nav-icon">🎒</div><div>Инв.</div></div>
    <div class="nav-item" onclick="switchTab('lifetime', this)"><div class="nav-icon">⏳</div><div>Жизнь</div></div>
    <div class="nav-item" onclick="switchTab('craft', this)"><div class="nav-icon">🔨</div><div>Крафт</div><span class="nav-badge" style="background:#3b82f6; font-size:7px;">SOON</span></div>
    <div class="nav-item" onclick="switchTab('shop', this)"><div class="nav-icon">🛒</div><div>Магазин</div></div>
    <div class="nav-item" onclick="switchTab('chat', this)"><div class="nav-icon">💬</div><div>Чат</div></div>
    <div class="nav-item" onclick="switchTab('news', this)"><div class="nav-icon">📰</div><div>Новости</div><span id="nav-news-badge" class="nav-badge" style="display:none;">!</span></div>
    <div class="nav-item" onclick="switchTab('top', this)"><div class="nav-icon">🏆</div><div>Топ</div></div>
    <div class="nav-item" onclick="switchTab('rules', this)"><div class="nav-icon">📜</div><div>Правила</div></div>
    <div class="nav-item" onclick="switchTab('profile', this)"><div class="nav-icon">👤</div><div>Профиль</div><span id="nav-inbox-badge" class="nav-badge" style="display:none;">!</span></div>
    <div class="nav-item" id="nav-admin" style="display:none;" onclick="switchTab('admin', this)"><div class="nav-icon">⚙️</div><div>Админ</div></div>
  </div>
</div>

<script>
// ================= СУПЕР-ОПТИМИЗИРОВАННЫЙ JS =================
let itemsDB = [], casesDB = {}, newsDB = [];
const rolesDB = __ROLES_JSON__;
const VERSION = __VERSION_JSON__;
const VERSION_SUFFIX = __VERSION_SUFFIX_JSON__;
const SKIN_LIFETIME = __SKIN_LIFETIME_JSON__;
const LOADING_FACTS = __LOADING_FACTS_JSON__;
const XP_REWARDS = __XP_REWARDS_JSON__;
const START_BALANCE = __START_BALANCE_JSON__;

let currentUser = null, userRole = "user", isMuted = false;
let balance = 0, inventory = [], friends = [], incoming = [], outgoing = [], inbox = [];
let userXp = 0, userLevel = 1, userSponsor = 0, userHvh = false;
let currentMult = 1, currentCase = 'prisma';
let isSpinningArrow = false, authMode = 'login';
let skipRequested = false, animationTimeout = null;
let selectedSkinForAdmin = null;
let sl = null, sr = null;
let dropHistory = [];
try { dropHistory = JSON.parse(localStorage.getItem('dropHistory') || '[]'); } catch(e) { dropHistory = []; }
let confirmCallback = null;
let timerInterval = null;
let selectedInv = new Set();
let soundEnabled = localStorage.getItem('sound') !== 'off';
let lastChatSent = 0;
let selectedLifetimeDays = 1;
let shopPage = 1;
let shopPageSize = 24;

let audioCtx = null;
function beep(freq, dur, type, vol) {
  if (!soundEnabled) return;
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(); const g = audioCtx.createGain();
    o.type = type || 'sine'; o.frequency.value = freq; g.gain.value = vol || 0.03;
    o.connect(g); g.connect(audioCtx.destination); o.start(); o.stop(audioCtx.currentTime + dur);
  } catch(e) {}
}
function soundDrop() { beep(660, 0.1, 'square'); setTimeout(function(){ beep(880, 0.15, 'square'); }, 100); }
function soundWin() { beep(523, 0.1); setTimeout(function(){ beep(659, 0.1); }, 100); setTimeout(function(){ beep(784, 0.2); }, 200); }
function soundError() { beep(220, 0.2, 'sawtooth', 0.05); }
function soundLevelUp() { beep(523,0.15); setTimeout(function(){ beep(659,0.15); }, 150); setTimeout(function(){ beep(784,0.15); }, 300); setTimeout(function(){ beep(1046,0.3); }, 450); }

function toggleSound() { soundEnabled = !soundEnabled; localStorage.setItem('sound', soundEnabled ? 'on' : 'off'); showToast('Звук: ' + (soundEnabled ? 'ВКЛ' : 'ВЫКЛ'), 'info'); }
function setTheme(t) { document.body.className = t; localStorage.setItem('theme', t); }
const savedTheme = localStorage.getItem('theme') || 'theme-dark'; document.body.className = savedTheme;

function setCookie(n, v, d) { const dt = new Date(); dt.setTime(dt.getTime() + d * 24 * 60 * 60 * 1000); document.cookie = n + "=" + v + ";expires=" + dt.toUTCString() + ";path=/;SameSite=Lax"; }
function getCookie(n) { const c = n + "=", dc = decodeURIComponent(document.cookie), ca = dc.split(';'); for (let i = 0; i < ca.length; i++) { let x = ca[i].trim(); if (x.indexOf(c) === 0) return x.substring(c.length); } return ""; }
function logout() {
  setCookie("user", "", -1);
  localStorage.removeItem('dropHistory');
  localStorage.removeItem('dailyDiscount');
  localStorage.removeItem('rouletteLast');
  localStorage.removeItem('readNews');
  location.reload();
}

function toggleAuthMode() {
  authMode = authMode === 'login' ? 'register' : 'login';
  document.getElementById('auth-title').innerText = authMode === 'login' ? 'Вход' : 'Регистрация';
  document.getElementById('auth-submit').innerText = authMode === 'login' ? 'Войти' : 'Создать аккаунт';
  document.getElementById('auth-switch').innerText = authMode === 'login' ? 'Нет аккаунта? Регистрация' : 'Уже есть аккаунт? Войти';
  document.getElementById('auth-error').style.display = 'none';
}

async function submitAuth() {
  const login = document.getElementById('auth-login').value.trim();
  const pwd = document.getElementById('auth-password').value;
  const hvh = document.getElementById('auth-hvh').checked;
  const errBox = document.getElementById('auth-error');
  errBox.style.display = 'none';
  if (!login || !pwd) { errBox.innerText = "Заполните все поля!"; errBox.style.display = 'block'; return; }
  const ep = authMode === 'login' ? '/api/login' : '/api/register';
  const payload = {login: login, password: pwd};
  if (authMode === 'register') payload.hvh = hvh;
  try {
    const res = await fetch(ep, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (data.success) {
      if (authMode === 'login') { setCookie("user", login, 7); applyUser(login, data.user); initApp(); }
      else { alert("Аккаунт создан! Войдите."); toggleAuthMode(); }
    } else { errBox.innerText = data.message; errBox.style.display = 'block'; }
  } catch (e) { errBox.innerText = "Ошибка: " + e.message; errBox.style.display = 'block'; }
}

function applyUser(login, user) {
  currentUser = login; balance = user.balance; inventory = user.inventory || [];
  userRole = user.role || 'user'; friends = user.friends || []; incoming = user.incoming || [];
  outgoing = user.outgoing || []; isMuted = user.muted || false;
  inbox = user.inbox || [];
  userXp = user.xp || 0; userLevel = user.level || 1;
  userSponsor = user.sponsor || 0; userHvh = user.hvh || false;
}

function updateXpDisplay() {
  document.getElementById('xp-display').innerText = 'Lvl ' + userLevel + ' · ' + userXp + ' XP';
}

window.onload = async function() {
  try {
    const ok = await loadGameData();
    if (!ok) return;
    stopFactRotation();
    renderNewsList();
    const savedUser = getCookie("user");
    if (savedUser) {
      try {
        const res = await fetch('/api/check_cookie', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({login: savedUser}) });
        const data = await res.json();
        if (data.success) { applyUser(savedUser, data.user); initApp(); return; }
      } catch(e) { console.error('check_cookie', e); }
    }
    document.getElementById('boot-overlay').classList.add('hidden');
    document.getElementById('auth-screen').style.display = 'flex';
  } catch(e) {
    console.error('onload', e);
    stopFactRotation();
    var eEl = document.getElementById('boot-err');
    eEl.style.display = 'block'; eEl.innerText = 'Ошибка инициализации: ' + e.message;
    document.getElementById('boot-retry').style.display = 'inline-block';
  }
};

function initApp() {
  document.getElementById('username-display').innerText = currentUser;
  if (['admin','betatester','gl_moder','moder'].includes(userRole)) {
    document.getElementById('nav-admin').style.display = 'flex';
    if (['admin','betatester'].includes(userRole)) {
      document.getElementById('admin-only-admin').style.display = 'block';
      document.getElementById('admin-only-admin-2').style.display = 'block';
    }
  }
  const rm = rolesDB[userRole] || rolesDB['user'];
  const rb = document.getElementById('role-badge-header');
  rb.innerHTML = rm.icon + ' ' + rm.label;
  rb.style.background = rm.color;
  rb.style.display = 'inline-flex';
  if (isMuted) {
    document.getElementById('chat-input-area').style.display = 'none';
    document.getElementById('chat-muted-msg').style.display = 'block';
  }
  document.getElementById('boot-overlay').classList.add('hidden');
  document.getElementById('auth-screen').style.display = 'none';
  document.getElementById('main-app').style.display = 'flex';
  updateBalance();
  updateXpDisplay();
  renderCases();
  updateOpenButton();
  renderInventory();
  renderLifetime();
  renderProfile();
  renderDropHistory();
  renderDailyDiscount();
  loadChat();
  loadUserList();
  renderNewsList();
  updateNewsBadge();
  renderInbox();
  refreshAll();
  setInterval(refreshAll, 8000);
  timerInterval = setInterval(tickSkinTimers, 1000);
  setInterval(loadChat, 5000);
}

// ================= ВСПОМОГАТЕЛЬНЫЕ =================
function renderInbox() {
  const l = document.getElementById('inbox-list');
  document.getElementById('inbox-count').innerText = inbox.length;
  const badge = document.getElementById('nav-inbox-badge');
  if (badge) badge.style.display = inbox.length > 0 ? 'block' : 'none';
  if (!inbox.length) { l.innerHTML = '<div style="color:#666;font-size:12px;">Пока нет сообщений.</div>'; return; }
  l.innerHTML = inbox.slice().reverse().map(function(m) {
    return '<div class="inbox-item"><div class="meta"><b>От: ' + m.from + '</b> <span>' + (m.time || '') + '</span></div><div>' + m.text + '</div></div>';
  }).join('');
}

function tickSkinTimers() {
  const now = Math.floor(Date.now() / 1000);
  let anyExpired = false;
  inventory = inventory.filter(function(it) {
    if (it.expires_at && it.expires_at <= now) { anyExpired = true; return false; }
    return true;
  });
  if (anyExpired) { renderInventory(); renderLifetime(); renderProfile(); showToast('⏳ Некоторые скины истекли и удалены', 'warn'); }
  document.querySelectorAll('[data-expires]').forEach(function(el) {
    const exp = parseInt(el.getAttribute('data-expires'));
    const left = exp - now;
    if (left <= 0) return;
    const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
    el.textContent = '⏳ ' + h + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
    if (left < 3600) el.classList.remove('long'); else el.classList.add('long');
  });
}

async function refreshAll() {
  try {
    const res = await fetch('/api/check_cookie', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({login: currentUser}) });
    const data = await res.json();
    if (data.success) {
      const prevIncoming = incoming.length;
      const prevInbox = inbox.length;
      const prevInvLen = inventory.length;
      const prevLevel = userLevel;
      applyUser(currentUser, data.user);
      updateBalance(); renderProfile(); updateXpDisplay();
      if (data.user.inventory.length !== prevInvLen) { renderInventory(); renderLifetime(); }
      if (incoming.length > prevIncoming) { showToast("📥 Новая заявка в друзья!", "warn"); soundDrop(); }
      if (inbox.length > prevInbox) { showToast("📬 Новое сообщение в ЛС!", "success"); soundWin(); renderInbox(); }
      if (userLevel > prevLevel) { showLevelUp(userLevel); }
    }
  } catch(e) {}
}

function showLevelUp(lvl) {
  soundLevelUp();
  const m = document.createElement('div'); m.className = 'confirm-modal active';
  m.innerHTML = '<div class="confirm-box" style="border-color:#eab308;"><h2 style="color:#eab308;">🏆 УРОВЕНЬ ПОВЫШЕН!</h2><p style="font-size:16px;">Ты достиг уровня <b>' + lvl + '</b>!</p><button class="btn-action" onclick="this.closest(\'.confirm-modal\').remove()">Круто!</button></div>';
  document.body.appendChild(m);
}

async function loadUserList() {
  try {
    const res = await fetch('/api/all_users');
    const data = await res.json();
    const dl = document.getElementById('all-users-list'); const dl2 = document.getElementById('all-users-list-2');
    const html = (data.users || []).map(function(u) { return '<option value="' + u + '">'; }).join('');
    if (dl) dl.innerHTML = html; if (dl2) dl2.innerHTML = html;
  } catch(e) { console.error('loadUserList', e); }
}

async function saveProgress() {
  if (!currentUser) return;
  try {
    await fetch('/api/save', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({login: currentUser, balance: balance, inventory: inventory, friends: friends}) });
  } catch(e) { console.error(e); }
}

function showToast(msg, type) {
  type = type || "info"; const c = document.getElementById('toast-container'); if (!c) return;
  const t = document.createElement('div'); t.className = 'toast ' + type;
  const icons = { error: '❌', success: '✅', info: 'ℹ️', warn: '⚠️', xp: '⭐' };
  t.innerHTML = '<span>' + (icons[type] || 'ℹ️') + '</span> <span>' + msg + '</span>';
  c.appendChild(t);
  setTimeout(function() { t.classList.add('show'); }, 10);
  setTimeout(function() { t.classList.remove('show'); setTimeout(function() { t.remove(); }, 300); }, 3200);
}

function showResult(title, items) {
  document.getElementById('res-title').innerText = title;
  const l = document.getElementById('res-items'); l.innerHTML = '';
  items.forEach(function(i) { 
    l.innerHTML += '<div class="result-item"><img src="' + i.image + '" onerror="this.style.display=\'none\'"><div class="name">' + i.name + '</div><div class="price">$' + i.price.toLocaleString() + '</div></div>'; 
  });
  document.getElementById('result-modal').classList.add('active');
}
function closeResult() { document.getElementById('result-modal').classList.remove('active'); }

function confirmAction(title, text, cb) {
  document.getElementById('confirm-title').innerText = title;
  document.getElementById('confirm-text').innerText = text;
  confirmCallback = cb; document.getElementById('confirm-modal').classList.add('active');
}
function confirmYes() { document.getElementById('confirm-modal').classList.remove('active'); if (confirmCallback) { const cb = confirmCallback; confirmCallback = null; cb(); } }
function confirmNo() { document.getElementById('confirm-modal').classList.remove('active'); confirmCallback = null; }

function promptModal(title, cb) {
  const m = document.createElement('div'); m.className = 'confirm-modal active';
  m.innerHTML = '<div class="confirm-box"><h3>' + title + '</h3><input id="prompt-in" style="width:100%;padding:10px;margin:10px 0;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:6px;"><div style="display:flex;gap:8px;"><button class="btn-action" id="prompt-ok">OK</button><button class="btn-action" style="background:#666;" id="prompt-cancel">Отмена</button></div></div>';
  document.body.appendChild(m); const input = m.querySelector('#prompt-in'); input.focus();
  m.querySelector('#prompt-ok').onclick = () => { const v = input.value; m.remove(); cb(v); };
  m.querySelector('#prompt-cancel').onclick = () => m.remove();
  input.onkeypress = e => { if (e.key === 'Enter') m.querySelector('#prompt-ok').click(); };
}

function updateBalance() { document.getElementById('balance-display').innerText = balance.toLocaleString('ru-RU'); }

function renderCases() {
  const c = document.getElementById('cases-container'); c.innerHTML = '';
  for (let k in casesDB) {
    c.innerHTML += '<div class="case-card ' + (currentCase === k ? 'active' : '') + '" onclick="changeCase(\'' + k + '\')"><div class="case-glow"></div><div style="font-size:16px;">📦</div><div style="font-size:10px;font-weight:bold;">' + casesDB[k].name + '</div><div style="font-size:9px;color:#22c55e;">$' + casesDB[k].price + '</div></div>';
  }
}
function changeCase(id) { currentCase = id; renderCases(); updateOpenButton(); }
function setMult(m, btn) { currentMult = m; document.querySelectorAll('.mult-btn').forEach(function(b) { b.classList.remove('active'); }); if (btn) btn.classList.add('active'); updateOpenButton(); }
function updateOpenButton() { if (!casesDB[currentCase]) return; const p = casesDB[currentCase].price * currentMult; document.getElementById('btn-open-case').innerText = 'Открыть ' + (currentMult > 1 ? 'x' + currentMult : '') + ' за $' + p.toLocaleString('ru-RU'); }

function switchTab(id, nav) {
  document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.nav-item').forEach(function(n) { n.classList.remove('active'); });
  document.getElementById('tab-' + id).classList.add('active');
  if (nav) nav.classList.add('active');
  if (nav && nav.scrollIntoView) { try { nav.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' }); } catch(e) {} }
  if (id === 'inventory') renderInventory();
  if (id === 'lifetime') renderLifetime();
  if (id === 'chat') loadChat();
  if (id === 'profile') { renderProfile(); renderInbox(); }
  if (id === 'shop') { shopPage = 1; renderShop(); renderDailyDiscount(); }
  if (id === 'top') renderTop();
  if (id === 'news') { renderNewsList(); markNewsRead(); }
  if (id === 'admin') loadAdminData();
}

function getRandomItemByRarity(r) {
  const p = itemsDB.filter(function(i) { return i.rarity === r; });
  return p.length ? JSON.parse(JSON.stringify(p[Math.floor(Math.random() * p.length)])) : JSON.parse(JSON.stringify(itemsDB[0]));
}
function generateRandomRarity() {
  const w = casesDB[currentCase].drops; const t = Object.values(w).reduce(function(a, b) { return a + b; }, 0);
  let r = Math.random() * t; for (let k in w) { if (r < w[k]) return k; r -= w[k]; } return 'common';
}
function stampItem(it) { it.expires_at = Math.floor(Date.now() / 1000) + SKIN_LIFETIME; it.count = 1; return it; }

function openCase() {
  if (!casesDB[currentCase]) { showToast("Кейс не найден", "error"); return; }
  const tp = casesDB[currentCase].price * currentMult;
  if (balance < tp) { showToast("Мало денег!", "error"); soundError(); return; }
  balance -= tp; updateBalance();
  const btn = document.getElementById('btn-open-case'); btn.disabled = true; btn.innerText = "Крутится...";
  document.getElementById('btn-skip').style.display = 'block';
  skipRequested = false; clearTimeout(animationTimeout);

  const strip = document.getElementById('strip'); strip.innerHTML = '';
  const isMobile = window.innerWidth < 520;
  const itemWidth = isMobile ? 76 : 100;
  const visibleItems = isMobile ? 30 : 50;
  const winnerIndex = Math.floor(visibleItems / 2);

  let won = []; for (let i = 0; i < currentMult; i++) won.push(getRandomItemByRarity(generateRandomRarity()));
  let ws = won[0], html = '';
  for (let i = 0; i < visibleItems; i++) {
    let it = (i === winnerIndex) ? ws : getRandomItemByRarity(generateRandomRarity());
    html += '<div class="item rarity-' + it.rarity + '"><img src="' + it.image + '" onerror="this.style.display=\'none\'; this.nextElementSibling.style.display=\'block\';"><div class="item-icon" style="display:none;">' + it.icon + '</div><div>' + it.name + '</div></div>';
  }
  strip.innerHTML = html; strip.style.transition = 'none'; strip.style.transform = 'translate3d(0px,0,0)';

  animationTimeout = setTimeout(function() {
    if (skipRequested) return;
    const cw = document.getElementById('roulette-container').offsetWidth;
    const co = cw / 2, ro = (Math.random() * 40) - 20;
    const tp2 = (winnerIndex * itemWidth) - co + (itemWidth / 2) + ro;
    strip.style.transition = 'transform 3s cubic-bezier(0.1,0.7,0.1,1)';
    strip.style.transform = 'translate3d(-' + tp2 + 'px,0,0)';
    setTimeout(function() { if (skipRequested) return; finishOpen(won); }, 3050);
  }, 50);
}
function finishOpen(won) {
  won.forEach(function(i) { stampItem(i); 
    const existing = inventory.find(x => x.id === i.id);
    if (existing) existing.count = (existing.count || 1) + 1;
    else inventory.push(i);
  });
  won.forEach(function(i) { dropHistory.unshift({ name: i.name, icon: i.icon, image: i.image, price: i.price, rarity: i.rarity, time: Date.now() }); });
  dropHistory = dropHistory.slice(0, 20);
  try { localStorage.setItem('dropHistory', JSON.stringify(dropHistory)); } catch(e) {}
  showResult("🎉 Вы выбили:", won); soundWin();
  document.getElementById('btn-open-case').disabled = false; document.getElementById('btn-skip').style.display = 'none';
  updateOpenButton(); renderInventory(); renderLifetime(); renderDropHistory(); renderProfile(); saveProgress();
  fetch('/api/stats', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, cases: currentMult, spent: casesDB[currentCase].price * currentMult, won: won.reduce(function(s, i) { return s + i.price; }, 0), xp_gain: XP_REWARDS.case_open }) }).catch(function(){});
  fetch('/api/log', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, action: 'Открытие кейса', details: casesDB[currentCase].name + ' (x' + currentMult + ')' }) }).catch(function(){});
}
function skipAnimation() {
  skipRequested = true; clearTimeout(animationTimeout);
  const strip = document.getElementById('strip'); strip.style.transition = 'none'; strip.style.transform = 'translate3d(0px,0,0)';
  let won = []; for (let i = 0; i < currentMult; i++) won.push(getRandomItemByRarity(generateRandomRarity()));
  finishOpen(won);
}
function renderDropHistory() {
  const h = document.getElementById('drop-history');
  if (!dropHistory.length) { h.innerHTML = '<div style="color:#666;text-align:center;padding:10px;">Пока пусто</div>'; return; }
  h.innerHTML = dropHistory.map(function(d) {
    const t = new Date(d.time).toLocaleTimeString('ru-RU');
    return '<div class="history-item rarity-' + d.rarity + '" style="border-left:3px solid var(--' + d.rarity + ');"><span style="display:flex;align-items:center;gap:4px;"><img src="' + (d.image||'') + '" onerror="this.style.display=\'none\'">' + d.name + '</span><span style="color:#22c55e;">$' + d.price.toLocaleString() + ' <small style="opacity:0.6;">' + t + '</small></span></div>';
  }).join('');
}

function calculateChance() {
  const t = document.getElementById('upg-chance'), b = document.getElementById('btn-upgrade-start'), f = document.getElementById('upg-fee'), circle = document.getElementById('upg-target-circle');
  if (!sl || !sr) { t.innerText = "0%"; f.innerText = "$0"; b.disabled = true; circle.style.strokeDasharray = "0 999"; return; }
  let c = (sl.price / sr.price) * 100; if (c > 90) c = 90; if (c < 1) c = 1;
  const circ = 534, dash = (c / 100) * circ;
  circle.style.strokeDasharray = dash + ' ' + circ;
  circle.style.strokeDashoffset = (circ / 2) - (dash / 2);
  let fee = Math.floor(sr.price * 0.2);
  f.innerText = '$' + fee.toLocaleString(); t.innerText = c.toFixed(1) + "%";
  t.style.color = c > 30 ? '#22c55e' : '#ef4444'; b.disabled = (balance < fee);
}
function autoPickTarget(mult) {
  if (!sl) { showToast("Сначала выберите свой скин (слева)", "warn"); return; }
  const targetPrice = sl.price * mult; let best = null, bestDiff = Infinity;
  itemsDB.forEach(function(it) { if (it.price <= sl.price) return; const diff = Math.abs(it.price - targetPrice); if (diff < bestDiff) { bestDiff = diff; best = it; } });
  if (!best) { showToast("Не нашёл подходящую цель", "error"); return; }
  sr = JSON.parse(JSON.stringify(best));
  document.getElementById('slot-right').innerHTML = '<img src="' + sr.image + '" onerror="this.style.display=\'none\'"><div style="font-size:9px;">$' + sr.price + '</div>';
  document.getElementById('slot-right').classList.add('filled'); calculateChance();
  showToast('Цель: ' + sr.name + ' ($' + sr.price.toLocaleString() + ')', 'success');
}
function openModal(s) {
  if (s === 'right' && !sl) { showToast("Сначала выберите свой предмет!", "error"); return; }
  const m = document.getElementById('modal-select'), g = document.getElementById('modal-grid');
  document.getElementById('modal-title').innerText = s === 'left' ? "Выбери свой предмет" : "Выбери цель (дороже)";
  g.innerHTML = '';
  let list = s === 'left' ? inventory : itemsDB.filter(function(i) { return i.price > sl.price; });
  if (!list.length) g.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#666;">Нет предметов</div>';
  list.forEach(function(it, idx) {
    const di = s === 'left' ? idx : itemsDB.findIndex(function(i) { return i.name === it.name; });
    g.innerHTML += '<div class="inv-item rarity-' + it.rarity + '" onclick="selectItemForUpgrade(\'' + s + '\',' + di + ',' + (s === 'left') + ')"><img src="' + it.image + '" onerror="this.style.display=\'none\'"><div style="font-size:9px;margin-top:5px;font-weight:bold;">' + it.name + '</div><div style="color:#22c55e;font-size:10px;">$' + it.price.toLocaleString() + '</div></div>';
  });
  m.classList.add('active');
}
function closeModal() { document.getElementById('modal-select').classList.remove('active'); }
function selectItemForUpgrade(s, i, fromInv) {
  const it = fromInv ? inventory[i] : itemsDB[i]; if (!it) return;
  if (s === 'left') {
    sl = it; document.getElementById('slot-left').innerHTML = '<img src="' + it.image + '" onerror="this.style.display=\'none\'"><div style="font-size:9px;">$' + it.price + '</div>';
    document.getElementById('slot-left').classList.add('filled');
    if (sr && sr.price <= sl.price) { sr = null; document.getElementById('slot-right').innerHTML = '<div style="font-size:30px;">🎯</div><div>Цель</div>'; document.getElementById('slot-right').classList.remove('filled'); }
  } else {
    sr = it; document.getElementById('slot-right').innerHTML = '<img src="' + sr.image + '" onerror="this.style.display=\'none\'"><div style="font-size:9px;">$' + sr.price + '</div>';
    document.getElementById('slot-right').classList.add('filled');
  }
  closeModal(); calculateChance();
}
function startUpgrade() {
  if (!sl || !sr || isSpinningArrow) return;
  let fee = Math.floor(sr.price * 0.2);
  if (balance < fee) { showToast("Мало денег!", "error"); return; }
  isSpinningArrow = true;
  const b = document.getElementById('btn-upgrade-start'), arrow = document.getElementById('upg-arrow'), t = document.getElementById('upg-chance');
  b.disabled = true; b.innerText = "Крутится...";
  const chance = (sl.price / sr.price) * 100;
  const randomAngle = Math.random() * 360;
  const isWin = randomAngle <= (chance / 100) * 360;
  const totalRotation = 1800 + randomAngle;
  arrow.style.transition = 'none'; arrow.style.transform = 'translateX(-50%) rotate(0deg)';
  setTimeout(function() { arrow.style.transition = 'transform 3s cubic-bezier(0.15, 0.85, 0.15, 1)'; arrow.style.transform = 'translateX(-50%) rotate(' + totalRotation + 'deg)'; }, 50);
  let cnt = 0; const ti = setInterval(function() { t.innerText = Math.floor(Math.random() * 100) + "%"; cnt++; if (cnt > 25) clearInterval(ti); }, 50);
  setTimeout(function() {
    clearInterval(ti); t.innerText = chance.toFixed(1) + "%"; balance -= fee; updateBalance();
    if (isWin) {
      const invIndex = inventory.findIndex(function(i) { return i.name === sl.name && i.price === sl.price; });
      if (invIndex > -1) {
        if (inventory[invIndex].count > 1) inventory[invIndex].count--;
        else inventory.splice(invIndex, 1);
      }
      const newItem = stampItem(JSON.parse(JSON.stringify(sr))); 
      const existing = inventory.find(x => x.id === newItem.id);
      if (existing) existing.count = (existing.count || 1) + 1;
      else inventory.push(newItem);
      showResult("🔥 УСПЕХ! Вы получили:", [newItem]); soundWin();
      fetch('/api/stats', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, upgrades: 1, upgrade_wins: 1, xp_gain: XP_REWARDS.upgrade_win }) }).catch(function(){});
    } else {
      const invIndex = inventory.findIndex(function(i) { return i.name === sl.name && i.price === sl.price; });
      if (invIndex > -1) {
        if (inventory[invIndex].count > 1) inventory[invIndex].count--;
        else inventory.splice(invIndex, 1);
      }
      showToast('💀 ПРОВАЛ! Вы потеряли скин и залог $' + fee.toLocaleString(), "error"); soundError();
      fetch('/api/stats', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, upgrades: 1, upgrade_wins: 0, xp_gain: XP_REWARDS.upgrade_fail }) }).catch(function(){});
    }
    isSpinningArrow = false; b.innerText = "Крутить"; sl = null; sr = null;
    document.getElementById('slot-left').innerHTML = '<div style="font-size:30px;">➕</div><div>Твой предмет</div>';
    document.getElementById('slot-left').classList.remove('filled');
    document.getElementById('slot-right').innerHTML = '<div style="font-size:30px;">🎯</div><div>Цель</div>';
    document.getElementById('slot-right').classList.remove('filled');
    calculateChance(); renderInventory(); renderLifetime(); saveProgress();
    refreshAll();
  }, 3100);
}

const RARITY_ORDER = { common: 0, uncommon: 1, rare: 2, epic: 3, legendary: 4 };
function renderInventory() {
  const g = document.getElementById('inventory-grid');
  const q = (document.getElementById('inv-search').value || '').toLowerCase();
  const sort = document.getElementById('inv-sort').value;
  const now = Math.floor(Date.now() / 1000);
  let list = inventory.map(function(it, idx) { return Object.assign({}, it, { _idx: idx }); });
  if (q) list = list.filter(function(it) { return it.name.toLowerCase().indexOf(q) !== -1; });
  if (sort === 'expires') list.sort(function(a, b) { return (a.expires_at || 0) - (b.expires_at || 0); });
  if (sort === 'price_desc') list.sort(function(a, b) { return b.price - a.price; });
  if (sort === 'price_asc') list.sort(function(a, b) { return a.price - b.price; });
  if (sort === 'rarity') list.sort(function(a, b) { return RARITY_ORDER[b.rarity] - RARITY_ORDER[a.rarity]; });
  if (sort === 'name') list.sort(function(a, b) { return a.name.localeCompare(b.name); });

  let total = 0; inventory.forEach(function(it) { total += it.price * (it.count || 1); });
  document.getElementById('inv-count').innerText = inventory.reduce((s, it) => s + (it.count || 1), 0);
  document.getElementById('inv-total').innerText = total.toLocaleString('ru-RU');

  if (!list.length) { g.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#666;padding:20px;">Пусто</div>'; return; }
  g.innerHTML = list.map(function(it) {
    let timerHtml = '';
    if (it.expires_at) {
      const left = it.expires_at - now;
      if (left > 0) {
        const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
        const cls = left < 3600 ? '' : 'long';
        timerHtml = '<div class="skin-timer ' + cls + '" data-expires="' + it.expires_at + '">⏳ ' + h + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0') + '</div>';
      }
    }
    const isSel = selectedInv.has(it._idx) ? ' selected' : '';
    const countBadge = (it.count || 1) > 1 ? '<div class="count-badge">×' + it.count + '</div>' : '';
    return '<div class="inv-item rarity-' + it.rarity + isSel + '" onclick="handleInvClick(' + it._idx + ')">' + timerHtml + countBadge + '<img src="' + it.image + '" onerror="this.style.display=\'none\'; this.nextElementSibling.style.display=\'block\';"><div style="font-size:25px;display:none;">' + it.icon + '</div><div style="font-size:9px;margin-top:5px;font-weight:bold;height:22px;overflow:hidden;">' + it.name + '</div><div style="color:#22c55e;font-size:10px;">$' + it.price.toLocaleString() + '</div></div>';
  }).join('');
}
function handleInvClick(idx) { if (selectedInv.size > 0) toggleSelectInv(idx); else sellItem(idx); }
function toggleSelectInv(idx) { if (selectedInv.has(idx)) selectedInv.delete(idx); else selectedInv.add(idx); renderInventory(); renderLifetime(); }
function clearSelection() { selectedInv.clear(); renderInventory(); renderLifetime(); }
function selectAllInv() { inventory.forEach((_, i) => selectedInv.add(i)); renderInventory(); renderLifetime(); }
function sellItem(i) {
  const it = inventory[i]; if (!it) return;
  const totalPrice = it.price * (it.count || 1);
  confirmAction("Продать скин?", it.name + (it.count > 1 ? ' ×' + it.count : '') + ' за $' + totalPrice.toLocaleString(), function() {
    balance += totalPrice; inventory.splice(i, 1);
    updateBalance(); renderInventory(); renderLifetime(); renderProfile();
    showToast('Продано: ' + it.name + ' (+$' + totalPrice.toLocaleString() + ')', "success"); saveProgress();
    fetch('/api/stats', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, sold: (it.count || 1), xp_gain: XP_REWARDS.sell_skin }) }).catch(function(){});
  });
}
function sellAll() {
  if (!inventory.length) return;
  let total = inventory.reduce(function(s, it) { return s + it.price * (it.count || 1); }, 0);
  confirmAction("Продать ВСЁ?", 'Все скины на $' + total.toLocaleString(), function() {
    balance += total; inventory = []; selectedInv.clear();
    updateBalance(); renderInventory(); renderLifetime(); renderProfile();
    showToast('Продано всё! (+$' + total.toLocaleString() + ')', "success"); saveProgress();
  });
}
function sellSelected() {
  if (!selectedInv.size) { showToast('Ничего не выбрано', 'warn'); return; }
  const items = [...selectedInv].map(i => inventory[i]).filter(Boolean);
  const total = items.reduce((s, i) => s + i.price * (i.count || 1), 0);
  confirmAction('Продать выбранное?', items.length + ' позиций на $' + total.toLocaleString(), () => {
    balance += total; inventory = inventory.filter((_, i) => !selectedInv.has(i)); selectedInv.clear();
    updateBalance(); renderInventory(); renderLifetime(); renderProfile(); saveProgress(); showToast('+$' + total.toLocaleString(), 'success');
  });
}

function selectLifetimeDays(d) {
  selectedLifetimeDays = d;
  document.getElementById('lifetime-selected').innerText = d;
  renderLifetime();
}
function renderLifetime() {
  const g = document.getElementById('lifetime-grid');
  const now = Math.floor(Date.now() / 1000);
  if (!inventory.length) { g.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#666;padding:20px;">Инвентарь пуст</div>'; return; }
  g.innerHTML = inventory.map(function(it, idx) {
    const baseCost = Math.ceil(it.price * 1.3) * (it.count || 1);
    const totalCost = baseCost * selectedLifetimeDays;
    let timeStr = '∞';
    if (it.expires_at) {
      const left = it.expires_at - now;
      if (left > 0) {
        const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60);
        timeStr = h + 'ч ' + String(m).padStart(2,'0') + 'м';
      }
    }
    return '<div class="inv-item rarity-' + it.rarity + '" onclick="extendSkinLifetime(' + idx + ')">' +
      '<img src="' + it.image + '" onerror="this.style.display=\'none\'">' +
      '<div style="font-size:9px;margin-top:5px;font-weight:bold;height:22px;overflow:hidden;">' + it.name + (it.count > 1 ? ' ×' + it.count : '') + '</div>' +
      '<div style="font-size:9px;color:#ef4444;">Осталось: ' + timeStr + '</div>' +
      '<div style="font-size:9px;color:#22c55e;font-weight:bold;">+' + selectedLifetimeDays + 'д за $' + totalCost.toLocaleString() + '</div>' +
    '</div>';
  }).join('');
}
function extendSkinLifetime(idx) {
  const it = inventory[idx]; if (!it) return;
  const totalCost = Math.ceil(it.price * 1.3) * selectedLifetimeDays * (it.count || 1);
  if (balance < totalCost) { showToast('Нужно $' + totalCost.toLocaleString(), 'error'); return; }
  confirmAction('Продлить ' + it.name + '?', '+' + selectedLifetimeDays + ' дней за $' + totalCost.toLocaleString(), () => {
    balance -= totalCost;
    it.expires_at = (it.expires_at || Math.floor(Date.now()/1000)) + (selectedLifetimeDays * 86400);
    updateBalance(); renderInventory(); renderLifetime(); renderProfile(); saveProgress();
    showToast('Продлено на ' + selectedLifetimeDays + 'д', 'success');
  });
}
function extendAllWithDays() {
  if (!inventory.length) { showToast('Инвентарь пуст', 'warn'); return; }
  let total = 0;
  inventory.forEach(it => total += Math.ceil(it.price * 1.3) * selectedLifetimeDays * (it.count || 1));
  if (balance < total) { showToast('Нужно $' + total.toLocaleString(), 'error'); return; }
  confirmAction('Продлить все скины?', 'Все скины на +' + selectedLifetimeDays + 'д за $' + total.toLocaleString(), () => {
    balance -= total;
    inventory.forEach(it => { it.expires_at = (it.expires_at || Math.floor(Date.now()/1000)) + (selectedLifetimeDays * 86400); });
    updateBalance(); renderInventory(); renderLifetime(); renderProfile(); saveProgress();
    showToast('Все скины продлены на +' + selectedLifetimeDays + 'д', 'success');
  });
}
function extendSelectedWithDays() {
  if (!selectedInv.size) { showToast('Ничего не выбрано', 'warn'); return; }
  let total = 0;
  selectedInv.forEach(i => { if (inventory[i]) total += Math.ceil(inventory[i].price * 1.3) * selectedLifetimeDays * (inventory[i].count || 1); });
  if (balance < total) { showToast('Нужно $' + total.toLocaleString(), 'error'); return; }
  confirmAction('Продлить выбранные?', selectedInv.size + ' позиций на +' + selectedLifetimeDays + 'д за $' + total.toLocaleString(), () => {
    balance -= total;
    selectedInv.forEach(i => { if (inventory[i]) inventory[i].expires_at = (inventory[i].expires_at || Math.floor(Date.now()/1000)) + (selectedLifetimeDays * 86400); });
    selectedInv.clear();
    updateBalance(); renderInventory(); renderLifetime(); renderProfile(); saveProgress();
    showToast('Выбранные скины продлены', 'success');
  });
}

function getDailyDiscount() {
  const today = new Date().toDateString(); const saved = localStorage.getItem('dailyDiscount');
  if (saved) { try { const d = JSON.parse(saved); if (d.date === today) return d; } catch(e) {} }
  const d = { date: today, idx: Math.floor(Math.random() * itemsDB.length), pct: 20 + Math.floor(Math.random() * 30) };
  localStorage.setItem('dailyDiscount', JSON.stringify(d)); return d;
}
function renderDailyDiscount() {
  const el = document.getElementById('daily-discount'); if (!el) return;
  const d = getDailyDiscount(); const item = itemsDB[d.idx]; if (!item) return;
  const newPrice = Math.floor(item.price * (100 - d.pct) / 100);
  el.innerHTML = '🔥 <b>Скидка дня:</b> ' + item.name + ' — <s style="opacity:0.6;">$' + item.price.toLocaleString() + '</s> → <b style="color:#22c55e;">$' + newPrice.toLocaleString() + '</b> (-' + d.pct + '%)';
}
function renderShop() {
  const g = document.getElementById('shop-grid');
  const q = (document.getElementById('shop-search').value || '').toLowerCase();
  const r = document.getElementById('shop-rarity').value; const disc = getDailyDiscount();
  let list = itemsDB.filter(function(it) { return (!q || it.name.toLowerCase().indexOf(q) !== -1) && (!r || it.rarity === r); });
  
  const totalPages = Math.max(1, Math.ceil(list.length / shopPageSize));
  if (shopPage > totalPages) shopPage = totalPages;
  const start = (shopPage - 1) * shopPageSize;
  const pageList = list.slice(start, start + shopPageSize);
  
  if (!list.length) { g.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#666;padding:20px;">Ничего не найдено</div>'; document.getElementById('shop-pagination').style.display = 'none'; return; }
  
  g.innerHTML = pageList.map(function(it) {
    const idx = itemsDB.findIndex(function(x) { return x.name === it.name; }); const isDisc = idx === disc.idx;
    const finalPrice = isDisc ? Math.floor(it.price * (100 - disc.pct) / 100) : it.price;
    const priceHtml = isDisc ? '<div style="font-size:9px;color:#22c55e;">$' + finalPrice.toLocaleString() + ' 🔥</div>' : '<div style="color:#22c55e;font-size:10px;">$' + it.price.toLocaleString() + '</div>';
    return '<div class="inv-item rarity-' + it.rarity + '" onclick="buySkin(' + idx + ')"><img src="' + it.image + '" onerror="this.style.display=\'none\'"><div style="font-size:9px;margin-top:5px;font-weight:bold;height:22px;overflow:hidden;">' + it.name + '</div>' + priceHtml + '</div>';
  }).join('');
  
  const pag = document.getElementById('shop-pagination');
  if (totalPages > 1) {
    pag.style.display = 'flex';
    document.getElementById('shop-page-info').innerText = shopPage + ' / ' + totalPages;
  } else {
    pag.style.display = 'none';
  }
}
function changeShopPage(delta) {
  shopPage += delta;
  renderShop();
}
function buySkin(idx) {
  const it = itemsDB[idx]; const disc = getDailyDiscount(); const isDisc = idx === disc.idx;
  const finalPrice = isDisc ? Math.floor(it.price * (100 - disc.pct) / 100) : it.price;
  if (balance < finalPrice) { showToast("Мало денег! Нужно $" + finalPrice.toLocaleString(), "error"); soundError(); return; }
  confirmAction("Купить скин?", it.name + ' за $' + finalPrice.toLocaleString() + (isDisc ? ' 🔥 скидка!' : ''), function() {
    balance -= finalPrice; const copy = stampItem(JSON.parse(JSON.stringify(it))); 
    const existing = inventory.find(x => x.id === copy.id);
    if (existing) existing.count = (existing.count || 1) + 1;
    else inventory.push(copy);
    updateBalance(); renderProfile(); showToast('Куплено: ' + it.name, "success"); saveProgress();
  });
}

function renderProfile() {
  document.getElementById('prof-name').innerText = currentUser;
  document.getElementById('prof-balance').innerText = balance.toLocaleString('ru-RU');
  document.getElementById('prof-inv-count').innerText = inventory.reduce((s,it)=>s+(it.count||1),0);
  const val = inventory.reduce(function(s, i) { return s + i.price * (i.count || 1); }, 0);
  document.getElementById('prof-inv-value').innerText = val.toLocaleString('ru-RU');
  document.getElementById('prof-xp').innerHTML = '⭐ Уровень: <b>' + userLevel + '</b> · XP: <b>' + userXp + '</b>';

  const rm = rolesDB[userRole] || rolesDB['user'];
  let sponsorText = userSponsor > 0 ? ' · <span class="role-badge" style="background:#a855f7;">💎 Спонсор ' + userSponsor + '</span>' : '';
  document.getElementById('prof-role-rank').innerHTML = '<span class="role-badge" style="background:' + rm.color + ';">' + rm.icon + ' ' + rm.label + '</span>' + sponsorText + (userHvh ? ' <span class="role-badge" style="background:#dc2626;">🔥 HVH</span>' : '');

  const fl = document.getElementById('friends-list'); document.getElementById('friends-count').innerText = friends.length; fl.innerHTML = '';
  if (!friends.length) fl.innerHTML = '<div style="color:#666;">Пока нет друзей.</div>';
  else friends.forEach(function(f) { fl.innerHTML += '<div class="friend-item"><span><span class="online-dot"></span>' + f + '</span><div><button class="admin-btn btn-sm fixed" style="background:#22c55e;" onclick="sendPM(\'' + f + '\')">ЛС</button><button class="admin-btn btn-sm danger fixed" onclick="removeFriend(\'' + f + '\')">Удалить</button></div></div>'; });

  const inc = document.getElementById('incoming-list'); document.getElementById('incoming-count').innerText = incoming.length; inc.innerHTML = '';
  if (!incoming.length) inc.innerHTML = '<div style="color:#666;">Нет входящих заявок.</div>';
  else incoming.forEach(function(u) { inc.innerHTML += '<div class="friend-item"><span>' + u + '</span><div><button class="admin-btn btn-sm success fixed" onclick="acceptFriend(\'' + u + '\')">✓ Принять</button><button class="admin-btn btn-sm danger fixed" onclick="rejectFriend(\'' + u + '\')">✗</button></div></div>'; });

  const out = document.getElementById('outgoing-list'); document.getElementById('outgoing-count').innerText = outgoing.length; out.innerHTML = '';
  if (!outgoing.length) out.innerHTML = '<div style="color:#666;">Нет исходящих.</div>';
  else outgoing.forEach(function(u) { out.innerHTML += '<div class="friend-item"><span>' + u + ' <small style="opacity:0.6;">(ожидание)</small></span><button class="admin-btn btn-sm danger fixed" onclick="cancelRequest(\'' + u + '\')">Отменить</button></div>'; });

  fetch('/api/check_cookie', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({login: currentUser}) }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.success && d.user.stats) {
      const s = d.user.stats;
      document.getElementById('prof-stats').innerHTML = '🎁 Кейсов открыто: <b>' + (s.cases_opened || 0) + '</b><br>💸 Потрачено: <b style="color:#ef4444;">$' + (s.spent || 0).toLocaleString() + '</b><br>💰 Выиграно на: <b style="color:#22c55e;">$' + (s.won_value || 0).toLocaleString() + '</b><br>⬆️ Апгрейдов: <b>' + (s.upgrades || 0) + '</b> (побед: <b style="color:#22c55e;">' + (s.upgrade_wins || 0) + '</b>)<br>💬 Сообщений: <b>' + (s.chat_msgs || 0) + '</b><br>💵 Продаж: <b>' + (s.sold || 0) + '</b>';
      renderAchievements(s);
    }
  }).catch(function(){});
}

function getRankJS(bal) {
  if (bal >= 10000000) return { name: 'Бог', icon: '🌌', color: '#ffffff' };
  if (bal >= 5000000) return { name: 'Император', icon: '👑', color: '#ffd700' };
  if (bal >= 1000000) return { name: 'Легенда', icon: '🌟', color: '#ff8c00' };
  if (bal >= 500000) return { name: 'Мастер', icon: '💎', color: '#a855f7' };
  if (bal >= 250000) return { name: 'Элита', icon: '🔥', color: '#ef4444' };
  if (bal >= 100000) return { name: 'Профи', icon: '🥇', color: '#f59e0b' };
  if (bal >= 50000) return { name: 'Ветеран', icon: '🎖️', color: '#eab308' };
  if (bal >= 20000) return { name: 'Боец', icon: '⚔️', color: '#3b82f6' };
  if (bal >= 10000) return { name: 'Любитель', icon: '🥈', color: '#c0c0c0' };
  return { name: 'Новичок', icon: '🥉', color: '#cd7f32' };
}

function renderAchievements(s) {
  const list = [
    { name: '🎁 Первый кейс', done: (s.cases_opened || 0) >= 1 }, { name: '🎁 10 кейсов', done: (s.cases_opened || 0) >= 10 },
    { name: '🎁 100 кейсов', done: (s.cases_opened || 0) >= 100 }, { name: '⬆️ Первый апгрейд', done: (s.upgrades || 0) >= 1 },
    { name: '🔥 Первая победа в апгрейде', done: (s.upgrade_wins || 0) >= 1 }, { name: '💰 Баланс $10K', done: balance >= 10000 },
    { name: '💰 Баланс $100K', done: balance >= 100000 }, { name: '🎒 50 скинов', done: inventory.reduce((n,i)=>n+(i.count||1),0) >= 50 },
    { name: '💬 10 сообщений', done: (s.chat_msgs || 0) >= 10 }, { name: '💬 100 сообщений', done: (s.chat_msgs || 0) >= 100 }
  ];
  document.getElementById('achievements').innerHTML = list.map(function(a) { return '<span class="badge ' + (a.done ? 'unlocked' : 'locked') + '">' + a.name + '</span>'; }).join('');
}

async function sendFriendRequest() {
  const name = document.getElementById('friend-input').value.trim();
  if (!name || name === currentUser) { showToast("Некорректный логин", "error"); return; }
  if (friends.indexOf(name) !== -1) { showToast("Уже в друзьях", "error"); return; }
  if (outgoing.indexOf(name) !== -1) { showToast("Заявка уже отправлена", "warn"); return; }
  const res = await fetch('/api/friend_request', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ from: currentUser, to: name }) });
  const data = await res.json();
  if (data.success) { outgoing.push(name); document.getElementById('friend-input').value = ''; renderProfile(); showToast("Заявка отправлена", "success"); }
  else { showToast(data.message, "error"); }
}
async function acceptFriend(name) { const res = await fetch('/api/friend_accept', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, from: name }) }); const data = await res.json(); if (data.success) { showToast('✓ Принят ' + name, "success"); refreshAll(); } else showToast(data.message, "error"); }
async function rejectFriend(name) { const res = await fetch('/api/friend_reject', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, from: name }) }); const data = await res.json(); if (data.success) { showToast('Отклонено', "info"); refreshAll(); } }
async function cancelRequest(name) { const res = await fetch('/api/friend_cancel', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, to: name }) }); const data = await res.json(); if (data.success) { showToast('Отменено', "info"); refreshAll(); } }
async function removeFriend(name) { confirmAction("Удалить из друзей?", name, async function() { const res = await fetch('/api/friend_remove', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, friend: name }) }); const data = await res.json(); if (data.success) { showToast('Удалён', "info"); refreshAll(); } }); }
function sendPM(name) { promptModal('Сообщение для ' + name, function(text) { if (!text) return; fetch('/api/pm', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ from: currentUser, to: name, text: text }) }).then(function() { showToast('ЛС отправлено ' + name, "success"); }); }); }

async function loadChat() {
  try {
    const res = await fetch('/api/chat'); const msgs = await res.json();
    const box = document.getElementById('chat-messages');
    box.innerHTML = msgs.map(function(m) {
      const role = m.role || 'user'; const rm = rolesDB[role] || rolesDB['user'];
      let roleBadge = '<span class="badge-role" style="background:' + rm.color + ';">' + rm.icon + ' ' + rm.label + '</span>';
      let lvlBadge = '<span class="badge-role" style="background:#eab308;color:#000;">Lvl ' + (m.level||1) + '</span>';
      return '<div class="chat-msg ' + (m.muted ? 'muted' : '') + '">' + roleBadge + lvlBadge + '<b>' + m.user + ':</b> ' + m.text + '</div>';
    }).join('');
    box.scrollTop = box.scrollHeight;
  } catch(e) {}
}
async function sendChat() {
  const now = Date.now(); if (now - lastChatSent < 2000) { showToast('Не так быстро (антиспам)', 'warn'); return; }
  const input = document.getElementById('chat-input'); const text = input.value.trim(); if (!text) return;
  lastChatSent = now; input.value = '';
  await fetch('/api/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, text: text }) });
  loadChat();
  fetch('/api/stats', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, chat_msgs: 1, xp_gain: XP_REWARDS.chat_msg }) }).catch(function(){});
}

async function claimDaily() {
  const res = await fetch('/api/daily', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser }) });
  const data = await res.json();
  if (data.success) { balance = data.balance; updateBalance(); refreshAll(); showToast('+$' + data.amount + ' ежедневный бонус!', "success"); soundWin(); }
  else showToast(data.message, "warn");
}

async function dailyRoulette() {
  const now = Date.now(); const last = parseInt(localStorage.getItem('rouletteLast') || '0');
  if (now - last < 86400000) { const h = Math.floor((86400000 - (now - last)) / 3600000); showToast('Рулетка через ' + h + 'ч', 'warn'); return; }
  const prize = [100, 500, 1000, 5000, 0][Math.floor(Math.random() * 5)]; localStorage.setItem('rouletteLast', String(now));
  if (prize > 0) { balance += prize; updateBalance(); saveProgress(); showToast('🎡 Рулетка: +$' + prize, 'success'); soundWin(); }
  else { showToast('🎡 Рулетка: пусто 😢', 'warn'); }
}

async function redeemPromo() {
  const code = document.getElementById('promo-input').value.trim().toUpperCase(); if (!code) return;
  const res = await fetch('/api/promo_redeem', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user: currentUser, code: code }) });
  const data = await res.json();
  if (data.success) { balance = data.balance; updateBalance(); document.getElementById('promo-input').value = ''; showToast('+$' + data.amount + ' по промокоду!', "success"); soundWin(); refreshAll(); }
  else showToast(data.message, "error");
}

async function renderTop() {
  try {
    const res = await fetch('/api/top'); const data = await res.json();
    document.getElementById('top-balance').innerHTML = data.by_balance.map(function(u, i) { return '<div class="top-row"><span><span class="top-rank">#' + (i + 1) + '</span> ' + u.login + '</span><span style="color:#22c55e;">$' + u.balance.toLocaleString() + '</span></div>'; }).join('') || '<div style="color:#666;">Пусто</div>';
    document.getElementById('top-inventory').innerHTML = data.by_inventory.map(function(u, i) { return '<div class="top-row"><span><span class="top-rank">#' + (i + 1) + '</span> ' + u.login + '</span><span style="color:#22c55e;">$' + u.value.toLocaleString() + '</span></div>'; }).join('') || '<div style="color:#666;">Пусто</div>';
    document.getElementById('top-level').innerHTML = data.by_level.map(function(u, i) { return '<div class="top-row"><span><span class="top-rank">#' + (i + 1) + '</span> ' + u.login + '</span><span style="color:#eab308;">Lvl ' + u.level + ' (' + u.xp + ' XP)</span></div>'; }).join('') || '<div style="color:#666;">Пусто</div>';
  } catch(e) {}
}

function renderNewsList() {
  const list = document.getElementById('news-list'); if (!list) return;
  const read = getReadNews();
  list.innerHTML = newsDB.map(function(n) {
    const isNew = n.badge === 'NEW' && read.indexOf(n.id) === -1;
    return '<div class="news-card">' + (isNew ? '<span class="news-badge">NEW</span>' : '') + '<h4>' + n.title + '</h4><div class="news-date">v' + n.version + ' · ' + n.date + '</div><p>' + n.text + '</p></div>';
  }).join('');
}
function getReadNews() { try { return JSON.parse(localStorage.getItem('readNews') || '[]'); } catch(e) { return []; } }
function markNewsRead() { const ids = newsDB.map(function(n) { return n.id; }); localStorage.setItem('readNews', JSON.stringify(ids)); updateNewsBadge(); }
function updateNewsBadge() {
  const read = getReadNews(); const unread = newsDB.filter(function(n) { return n.badge === 'NEW' && read.indexOf(n.id) === -1; }).length;
  const b1 = document.getElementById('nav-news-badge'); const b2 = document.getElementById('news-badge');
  if (b1) b1.style.display = unread > 0 ? 'block' : 'none'; if (b2) b2.style.display = unread > 0 ? 'block' : 'none';
}
function openNewsModal() {
  document.getElementById('news-ver').innerText = VERSION + ' ' + VERSION_SUFFIX;
  const recent = newsDB.slice(0, 3);
  document.getElementById('news-modal-list').innerHTML = recent.map(function(n) { return '<div class="news-card"><h4>' + n.title + '</h4><div class="news-date">v' + n.version + ' · ' + n.date + '</div><p>' + n.text + '</p></div>'; }).join('');
  document.getElementById('news-modal').classList.add('active');
}
function closeNewsModal() { document.getElementById('news-modal').classList.remove('active'); markNewsRead(); }

function renderSkinPicker() {
  const q = (document.getElementById('skin-search').value || '').toLowerCase();
  const r = document.getElementById('skin-rarity-filter').value;
  const g = document.getElementById('skin-picker-grid');
  let list = itemsDB.filter(function(it) { return (!q || it.name.toLowerCase().indexOf(q) !== -1) && (!r || it.rarity === r); });
  if (!list.length) { g.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:#666;padding:10px;">Ничего не найдено</div>'; return; }
  g.innerHTML = list.slice(0, 80).map(function(it) {
    const idx = itemsDB.findIndex(function(x) { return x.name === it.name; });
    const sel = selectedSkinForAdmin && selectedSkinForAdmin.name === it.name ? 'selected' : '';
    return '<div class="skin-pick rarity-' + it.rarity + ' ' + sel + '" onclick="pickSkinForAdmin(' + idx + ')"><img src="' + it.image + '" onerror="this.style.display=\'none\'"><div style="margin-top:3px;height:20px;overflow:hidden;">' + it.name + '</div><div style="color:#22c55e;">$' + it.price.toLocaleString() + '</div></div>';
  }).join('');
  if (list.length > 80) g.innerHTML += '<div style="grid-column:1/-1;text-align:center;color:#666;padding:10px;font-size:12px;">Показано 80 из ' + list.length + ' (используйте поиск)</div>';
}
function pickSkinForAdmin(idx) {
  selectedSkinForAdmin = itemsDB[idx];
  document.getElementById('skin-preview').innerHTML = '<b>Выбран:</b> ' + selectedSkinForAdmin.name + ' · редкость: <b>' + selectedSkinForAdmin.rarity + '</b> · цена: <b style="color:#22c55e;">$' + selectedSkinForAdmin.price.toLocaleString() + '</b>';
  renderSkinPicker();
}

async function loadAdminData() {
  if (!['admin','betatester','gl_moder','moder'].includes(userRole)) return;
  loadAdminLogs();
  if (['admin','betatester'].includes(userRole)) {
    loadAdminUsers(); loadPromos(); loadServerStats(); renderSkinPicker();
  }
}
async function loadAdminLogs() {
  try {
    const res = await fetch('/api/get_logs'); const logs = await res.json();
    document.getElementById('admin-logs').innerHTML = logs.slice().reverse().map(function(l) { return '<div class="log-item"><span class="time">' + l.time + '</span><span class="user">' + l.user + '</span><span class="action">' + l.action + '</span>' + l.details + '</div>'; }).join('');
  } catch(e) {}
}
async function loadAdminUsers() {
  try {
    const res = await fetch('/api/all_users_full'); const data = await res.json();
    document.getElementById('admin-users').innerHTML = (data.users || []).map(function(u) { return '<div class="top-row"><span><b>' + u.login + '</b> <small style="opacity:0.6;">(' + u.role + ' · Lvl ' + u.level + ')</small></span><span style="color:#22c55e;">$' + u.balance.toLocaleString() + ' · ' + u.inv_count + ' ск.</span></div>'; }).join('');
  } catch(e) {}
}
async function loadPromos() {
  try {
    const res = await fetch('/api/promos'); const data = await res.json(); const list = document.getElementById('promo-list'); const keys = Object.keys(data.promos);
    if (!keys.length) { list.innerHTML = '<div style="color:#666;font-size:12px;">Нет промокодов</div>'; return; }
    list.innerHTML = keys.map(function(k) { const p = data.promos[k]; return '<div class="promo-card"><span><b>' + k + '</b> · $' + p.amount + ' · ' + p.uses + '/' + p.limit + '</span><button class="admin-btn btn-sm danger fixed" onclick="deletePromo(\'' + k + '\')">Удалить</button></div>'; }).join('');
  } catch(e) {}
}
async function deletePromo(code) { await fetch('/api/promo_delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ code: code }) }); loadPromos(); }
async function loadServerStats() {
  try {
    const res = await fetch('/api/server_stats'); const s = await res.json();
    document.getElementById('server-stats').innerHTML = '👥 Пользователей: <b>' + s.users + '</b><br>💬 Сообщений в чате: <b>' + s.chat + '</b><br>📜 Логов: <b>' + s.logs + '</b><br>🎁 Кейсов открыто: <b>' + s.cases_opened + '</b><br>⬆️ Апгрейдов: <b>' + s.upgrades + '</b><br>🎟️ Промокодов: <b>' + s.promos + '</b>';
  } catch(e) {}
}
function broadcast() { promptModal('Текст рассылки для всех', async function(txt) { if (!txt) return; await fetch('/api/broadcast', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ from: currentUser, text: txt }) }); showToast('Рассылка отправлена в ЛС', 'success'); }); }

function deleteAccount() {
  confirmAction("Удалить аккаунт?", "Это действие необратимо. Весь прогресс будет утерян.", () => {
    promptModal("Введите пароль для подтверждения", async (pwd) => {
      if (!pwd) return;
      const res = await fetch('/api/delete_account', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ login: currentUser, password: pwd }) });
      const data = await res.json();
      if (data.success) { alert('Аккаунт удалён.'); logout(); }
      else showToast(data.message, 'error');
    });
  });
}

async function adminAction(action, extra) {
  if (!['admin','betatester','gl_moder','moder'].includes(userRole)) return;
  if (action === 'clear_chat' || action === 'purge_expired') { const msg = action === 'clear_chat' ? 'Очистить чат?' : 'Удалить все просроченные скины?'; confirmAction('Подтверждение', msg, () => doAdminAction(action, extra)); return; }
  doAdminAction(action, extra);
}
async function doAdminAction(action, extra) {
  let payload = { action: action, user: currentUser };
  if (action === 'give_money') { payload.target = document.getElementById('give-money-user').value; payload.amount = document.getElementById('give-money-amount').value; }
  if (action === 'take_money') { payload.target = document.getElementById('take-money-user').value; payload.amount = document.getElementById('take-money-amount').value; }
  if (action === 'give_skin') { payload.target = document.getElementById('give-skin-user').value; if (!selectedSkinForAdmin) { showToast("Выберите скин!", "error"); return; } payload.skin_name = selectedSkinForAdmin.name; payload.count = extra || 1; }
  if (action === 'set_role') { payload.target = document.getElementById('role-user').value; payload.role = extra; }
  if (action === 'set_sponsor') { payload.target = document.getElementById('sponsor-user').value; payload.level = extra; }
  if (['ban', 'mute', 'unban', 'reset_inventory', 'delete_user'].indexOf(action) !== -1) { payload.target = document.getElementById('punish-user').value || document.getElementById('danger-user').value; }
  if (action === 'create_promo') { payload.code = document.getElementById('promo-code').value.trim().toUpperCase(); payload.amount = document.getElementById('promo-amount').value; payload.limit = document.getElementById('promo-limit').value; }
  try {
    const res = await fetch('/api/admin_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) }); const data = await res.json();
    if (data.success) { showToast("✓ Выполнено", "success"); loadAdminData(); refreshAll(); } else showToast(data.message || "Ошибка", "error");
  } catch(e) { showToast("Ошибка сети", "error"); }
}
</script>
</body>
</html>
"""

HTML_PAGE = (HTML_PAGE
    .replace("__ROLES_JSON__", ROLES_JSON)
    .replace("__VERSION_JSON__", VERSION_JSON)
    .replace("__VERSION_SUFFIX_JSON__", VERSION_SUFFIX_JSON)
    .replace("__SKIN_LIFETIME_JSON__", SKIN_LIFETIME_JSON)
    .replace("__LOADING_FACTS_JSON__", LOADING_FACTS_JSON)
    .replace("__XP_REWARDS_JSON__", XP_REWARDS_JSON)
    .replace("__START_BALANCE_JSON__", START_BALANCE_JSON)
    .replace("__VERSION__", CURRENT_VERSION)
    .replace("__VERSION_SUFFIX__", VERSION_SUFFIX)
)

# ================= ОБРАБОТЧИК =================
class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def do_HEAD(self): self.send_response(200); self.end_headers()

    def do_GET(self):
        try:
            if self.path == '/':
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
                self.end_headers()
                self.wfile.write(HTML_PAGE.encode('utf-8'))
            elif self.path == '/api/skins_data': self._send_raw(SKINS_JSON_BYTES)
            elif self.path == '/api/cases_data': self._send_raw(CASES_JSON_BYTES)
            elif self.path == '/api/news_data': self._send_json(db.news)
            elif self.path == '/api/chat': self._send_json(db.chat)
            elif self.path == '/api/chat_hvh': self._send_json(db.chat_hvh)
            elif self.path == '/api/get_logs': self._send_json(db.logs)
            elif self.path == '/api/all_users': self._send_json({"users": list(db.users.keys())})
            elif self.path == '/api/all_users_full':
                users = []
                for k, u in db.users.items(): 
                    users.append({"login": k, "role": u.get("role", "user"), "balance": u.get("balance", 0), "inv_count": len(u.get("inventory", [])), "level": u.get("level", 1)})
                self._send_json({"users": users})
            elif self.path == '/api/promos': self._send_json({"promos": db.promos})
            elif self.path == '/api/top':
                visible = {k: u for k, u in db.users.items() if u.get("role") not in ["admin", "betatester"]}
                by_balance = sorted([{"login": k, "balance": u.get("balance", 0)} for k, u in visible.items()], key=lambda x: -x["balance"])[:10]
                by_inv = sorted([{"login": k, "value": sum(i.get("price", 0) * i.get("count", 1) for i in u.get("inventory", []))} for k, u in visible.items()], key=lambda x: -x["value"])[:10]
                by_level = sorted([{"login": k, "level": u.get("level", 1), "xp": u.get("xp", 0)} for k, u in visible.items()], key=lambda x: -x["xp"])[:10]
                self._send_json({"by_balance": by_balance, "by_inventory": by_inv, "by_level": by_level})
            elif self.path == '/api/server_stats':
                self._send_json({"users": len(db.users), "chat": len(db.chat), "logs": len(db.logs), "cases_opened": db.stats.get("cases_opened", 0), "upgrades": db.stats.get("upgrades", 0), "promos": len(db.promos)})
            else: self.send_response(404); self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}

            if self.path == '/api/register':
                hvh = data.get('hvh', False)
                ok, msg = db.register(data['login'], data['password'], hvh)
                self._send_json({"success": ok, "message": msg})
            elif self.path == '/api/login':
                ok, res = db.login(data['login'], data['password']); self._send_json({"success": ok, "user": res if ok else None, "message": res if not ok else ""})
            elif self.path == '/api/check_cookie':
                login = data.get('login')
                if login in db.users and not db.users[login].get("banned"): db.purge_expired(login); self._send_json({"success": True, "user": db.users[login]})
                else: self._send_json({"success": False})
            elif self.path == '/api/save':
                db.update_user(data['login'], {"balance": data['balance'], "inventory": data['inventory'], "friends": data.get('friends', [])})
                self._send_json({"success": True})
            elif self.path == '/api/chat':
                user = data.get('user')
                if user not in db.users: self._send_json({"success": False, "message": "Нет юзера"}); return
                u = db.users[user]
                msg = {"user": user, "text": data.get('text', ''), "muted": u.get("muted", False), "role": u.get("role", "user"), "level": u.get("level", 1)}
                db.add_chat(msg, hvh=u.get("hvh", False))
                self._send_json({"success": True})
            elif self.path == '/api/friend_request':
                frm, to = data.get('from'), data.get('to')
                if to not in db.users: self._send_json({"success": False, "message": "Пользователь не найден"}); return
                if frm == to: self._send_json({"success": False, "message": "Нельзя добавить себя"}); return
                if to in db.users[frm].get('friends', []): self._send_json({"success": False, "message": "Уже в друзьях"}); return
                if to in db.users[frm].get('outgoing', []): self._send_json({"success": False, "message": "Заявка уже отправлена"}); return
                if frm in db.users[to].get('incoming', []): self._send_json({"success": False, "message": "Заявка уже есть"}); return
                db.users[frm].setdefault('outgoing', []).append(to); db.users[to].setdefault('incoming', []).append(frm)
                db.save_users(); db.add_log(frm, "Заявка в друзья", "-> " + to); self._send_json({"success": True})
            elif self.path == '/api/friend_accept':
                user, frm = data.get('user'), data.get('from')
                if frm not in db.users[user].get('incoming', []): self._send_json({"success": False, "message": "Нет такой заявки"}); return
                db.users[user]['incoming'].remove(frm)
                if user in db.users[frm].get('outgoing', []): db.users[frm]['outgoing'].remove(user)
                db.users[user].setdefault('friends', []).append(frm); db.users[frm].setdefault('friends', []).append(user)
                db.users[user]['xp'] += XP_REWARDS["friend_accept"]
                db.save_users(); self._send_json({"success": True})
            elif self.path == '/api/friend_reject':
                user, frm = data.get('user'), data.get('from')
                if frm not in db.users[user].get('incoming', []): self._send_json({"success": False, "message": "Нет заявки"}); return
                db.users[user]['incoming'].remove(frm)
                if user in db.users[frm].get('outgoing', []): db.users[frm]['outgoing'].remove(user)
                db.save_users(); self._send_json({"success": True})
            elif self.path == '/api/friend_cancel':
                user, to = data.get('user'), data.get('to')
                if to not in db.users[user].get('outgoing', []): self._send_json({"success": False, "message": "Нет заявки"}); return
                db.users[user]['outgoing'].remove(to)
                if user in db.users[to].get('incoming', []): db.users[to]['incoming'].remove(user)
                db.save_users(); self._send_json({"success": True})
            elif self.path == '/api/friend_remove':
                user, fr = data.get('user'), data.get('friend')
                if fr in db.users[user].get('friends', []): db.users[user]['friends'].remove(fr)
                if user in db.users[fr].get('friends', []): db.users[fr]['friends'].remove(user)
                db.save_users(); self._send_json({"success": True})
            elif self.path == '/api/pm':
                frm, to, text = data.get('from'), data.get('to'), data.get('text', '')
                if to not in db.users: self._send_json({"success": False}); return
                db.users[to].setdefault('inbox', []).append({"from": frm, "text": text, "time": time.strftime("%H:%M")}); db.save_users(); self._send_json({"success": True})
            elif self.path == '/api/broadcast':
                text = data.get('text', '')
                for login in db.users:
                    if login != data.get('from'):
                        db.users[login].setdefault('inbox', []).append({"from": "SYSTEM", "text": text, "time": time.strftime("%H:%M")})
                db.save_users(); self._send_json({"success": True})
            elif self.path == '/api/daily':
                user = data.get('user')
                if user not in db.users: self._send_json({"success": False, "message": "Нет юзера"}); return
                now = int(time.time()); last = db.users[user].get('daily_last', 0)
                if now - last < 86400:
                    left = 86400 - (now - last); h = left // 3600; m = (left % 3600) // 60
                    self._send_json({"success": False, "message": "Через " + str(h) + "ч " + str(m) + "м"}); return
                amount = 1000; db.users[user]['balance'] += amount; db.users[user]['daily_last'] = now
                db.users[user]['xp'] += XP_REWARDS["daily"]
                db.save_users()
                self._send_json({"success": True, "amount": amount, "balance": db.users[user]['balance']})
            elif self.path == '/api/promo_redeem':
                user, code = data.get('user'), data.get('code', '').upper()
                if code not in db.promos: self._send_json({"success": False, "message": "Код не найден"}); return
                p = db.promos[code]
                if p.get('uses', 0) >= p.get('limit', 0): self._send_json({"success": False, "message": "Код исчерпан"}); return
                db.users[user]['balance'] += p['amount']; p['uses'] = p.get('uses', 0) + 1
                db.users[user]['xp'] += XP_REWARDS["promo"]
                db.save_users(); db.save_promos()
                self._send_json({"success": True, "amount": p['amount'], "balance": db.users[user]['balance']})
            elif self.path == '/api/log':
                db.add_log(data.get('user', '?'), data.get('action', ''), data.get('details', '')); self._send_json({"success": True})
            elif self.path == '/api/stats':
                user = data.get('user')
                xp_gain = data.get('xp_gain', 0)
                if user in db.users:
                    s = db.users[user].setdefault('stats', {})
                    for k in ['cases_opened', 'spent', 'won_value', 'upgrades', 'upgrade_wins', 'sold', 'chat_msgs']:
                        if k in data: s[k] = s.get(k, 0) + data[k]
                    if xp_gain > 0:
                        db.users[user]['xp'] += xp_gain
                        db.users[user]['level'] = calc_level(db.users[user]['xp'])
                    db.save_users()
                if 'cases' in data: db.stats['cases_opened'] = db.stats.get('cases_opened', 0) + data['cases']
                if 'upgrades' in data: db.stats['upgrades'] = db.stats.get('upgrades', 0) + data['upgrades']
                db.save_stats(); self._send_json({"success": True})
            elif self.path == '/api/delete_account':
                user, pwd = data.get('login'), data.get('password')
                if user not in db.users: self._send_json({"success": False, "message": "Нет юзера"}); return
                if db.users[user]['password'] != db._hash(pwd): self._send_json({"success": False, "message": "Неверный пароль"}); return
                if user == "admin": self._send_json({"success": False, "message": "Нельзя удалить админа"}); return
                del db.users[user]; db.save_users()
                self._send_json({"success": True})
            elif self.path == '/api/admin_action':
                action = data.get('action'); target = data.get('target')
                
                actor = data.get('user', '')
                actor_role = db.users.get(actor, {}).get('role', 'user')
                if actor_role == 'moder' and action not in ['ban', 'mute', 'unban', 'clear_chat', 'purge_expired']:
                    self._send_json({"success": False, "message": "Модераторам запрещено это действие"}); return
                if actor_role == 'gl_moder' and action not in ['ban', 'mute', 'unban', 'clear_chat', 'purge_expired']:
                    self._send_json({"success": False, "message": "Гл. модерам запрещено это действие"}); return

                if action == 'create_promo':
                    code = data.get('code', '').upper()
                    if not code: self._send_json({"success": False, "message": "Пустой код"}); return
                    db.promos[code] = {"amount": int(data.get('amount', 0)), "uses": 0, "limit": int(data.get('limit', 1))}
                    db.save_promos(); self._send_json({"success": True}); return
                if action == 'clear_chat': db.chat = []; db.save_chat(); self._send_json({"success": True}); return
                if action == 'purge_expired': total = db.purge_all(); self._send_json({"success": True, "removed": total}); return
                if target and target not in db.users: self._send_json({"success": False, "message": "Нет юзера"}); return
                if action == 'give_money': db.users[target]['balance'] += int(data.get('amount', 0))
                elif action == 'take_money': db.users[target]['balance'] -= int(data.get('amount', 0))
                elif action == 'give_skin':
                    skin_name = data.get('skin_name'); count = int(data.get('count', 1))
                    skin = next((s for s in ALL_SKINS if s['name'] == skin_name), None)
                    if not skin: self._send_json({"success": False, "message": "Скин не найден"}); return
                    now = int(time.time())
                    for _ in range(count):
                        existing = next((x for x in db.users[target]['inventory'] if x['id'] == skin['id']), None)
                        if existing: existing['count'] = existing.get('count', 1) + 1
                        else:
                            copy = json.loads(json.dumps(skin)); copy['expires_at'] = now + SKIN_LIFETIME; copy['count'] = 1
                            db.users[target]['inventory'].append(copy)
                elif action == 'ban': db.users[target]['banned'] = True
                elif action == 'unban': db.users[target]['banned'] = False; db.users[target]['muted'] = False
                elif action == 'mute': db.users[target]['muted'] = True
                elif action == 'set_role':
                    new_role = data.get('role', 'user')
                    if new_role not in ROLE_META: self._send_json({"success": False, "message": "Неизвестная роль"}); return
                    db.users[target]['role'] = new_role
                elif action == 'set_sponsor':
                    lvl = int(data.get('level', 0))
                    db.users[target]['sponsor'] = max(0, min(10, lvl))
                elif action == 'reset_inventory': db.users[target]['inventory'] = []
                elif action == 'delete_user':
                    if target == "admin": self._send_json({"success": False, "message": "Нельзя удалить админа"}); return
                    del db.users[target]
                else: self._send_json({"success": False, "message": "Неизвестное действие"}); return
                db.save_users(); self._send_json({"success": True})
            elif self.path == '/api/promo_delete':
                code = data.get('code', '').upper()
                if code in db.promos: del db.promos[code]; db.save_promos()
                self._send_json({"success": True})
            else: self.send_response(404); self.end_headers()
        except Exception as e:
            print(f"Server Error: {e}"); traceback.print_exc()
            try: self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode('utf-8'))
            except: pass

    def _send_json(self, obj):
        try:
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate'); self.end_headers()
            self.wfile.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass

    def _send_raw(self, raw_bytes):
        try:
            self.send_response(200); self.send_header('Content-type', 'application/json'); self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate'); self.end_headers()
            self.wfile.write(raw_bytes)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass

    def log_message(self, format, *args): pass

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except Exception: return "127.0.0.1"

def run_server():
    def open_browser():
        time.sleep(1.2)
        try: webbrowser.open(f'http://127.0.0.1:{PORT}')
        except Exception: pass
    threading.Thread(target=open_browser, daemon=True).start()

    def background_purger():
        while True:
            time.sleep(300)
            try: db.purge_all()
            except Exception as e: print(f"purge error: {e}")
    threading.Thread(target=background_purger, daemon=True).start()

    local_ip = get_local_ip()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), CustomHandler) as httpd:
        httpd.daemon_threads = True
        print(f"\n{'='*50}")
        print(f"🔫 Сервер запущен v{CURRENT_VERSION} {VERSION_SUFFIX}")
        print(f"{'='*50}")
        print(f"📱 На этом устройстве:  http://127.0.0.1:{PORT}/")
        print(f"🌍 По Wi-Fi (др. устройство): http://{local_ip}:{PORT}/")
        print(f"👑 admin/admin  |  🧪 betatester/beta  |  ⭐ gl_moder/glmoder  |  🛡 moder/moder")
        print(f"⏳ Скины живут 24 часа")
        print(f"{'='*50}\n")
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()