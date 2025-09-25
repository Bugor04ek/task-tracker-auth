# oauth_server.py
import os, sqlite3, secrets, time
from flask import Flask, request, jsonify, redirect
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ['GITHUB_OAUTH_CLIENT_ID']
CLIENT_SECRET = os.environ['GITHUB_OAUTH_CLIENT_SECRET']
REDIRECT_URI = os.environ['OAUTH_REDIRECT_URI']
SERVICE_SECRET = os.environ['OAUTH_SERVICE_SECRET']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
AUTH_ORG = os.environ.get('AUTH_ORG')
AUTH_TEAM_SLUG = os.environ.get('AUTH_TEAM_SLUG')  # optional
DB_PATH = os.environ.get('DATABASE_PATH', 'oauth.db')

OAUTH_BASE = "https://github.com/login/oauth"
API_BASE = "https://api.github.com"

app = Flask(__name__)

@app.route("/")
def index():
    return "OAuth server running! 🚀"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS states(
        state TEXT PRIMARY KEY, telegram_id INTEGER, created_at INTEGER)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        telegram_id INTEGER PRIMARY KEY,
        github_login TEXT, access_token TEXT, scopes TEXT, created_at INTEGER)""")
    conn.commit(); conn.close()

def save_state(state, tg_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO states(state, telegram_id, created_at) VALUES(?,?,?)",
                 (state, tg_id, int(time.time())))
    conn.commit(); conn.close()

def pop_state(state):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM states WHERE state=?", (state,))
    row = cur.fetchone()
    if not row:
        conn.close(); return None
    tg_id = row[0]
    cur.execute("DELETE FROM states WHERE state=?", (state,))
    conn.commit(); conn.close()
    return tg_id

def save_user(tg_id, login, token, scopes):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO users(telegram_id, github_login, access_token, scopes, created_at) VALUES(?,?,?,?,?)",
                 (tg_id, login, token, scopes, int(time.time())))
    conn.commit(); conn.close()

def is_authorized(tg_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT github_login FROM users WHERE telegram_id=?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

init_db()

@app.route('/create_state', methods=['POST'])
def create_state():
    secret = request.headers.get('X-SERVICE-SECRET')
    if secret != SERVICE_SECRET:
        return jsonify({"error":"unauthorized"}), 403
    data = request.get_json(force=True)
    tg = data.get('telegram_id')
    if not tg:
        return jsonify({"error":"telegram_id required"}), 400
    state = secrets.token_urlsafe(32)
    save_state(state, int(tg))
    auth_url = (
        f"{OAUTH_BASE}/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=read:org"
        f"&state={state}"
    )
    return jsonify({"auth_url": auth_url, "state": state})

@app.route('/callback')
def callback():
    code = request.args.get('code')
    state = request.args.get('state')
    if not code or not state:
        return "Missing code/state", 400
    tg_id = pop_state(state)
    if not tg_id:
        return "Invalid or expired state", 400

    token_resp = requests.post(
        f"{OAUTH_BASE}/access_token",
        headers={"Accept":"application/json"},
        data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": code, "redirect_uri": REDIRECT_URI},
        timeout=10
    )
    token_json = token_resp.json()
    access_token = token_json.get("access_token")
    scopes = token_json.get("scope", "")

    if not access_token:
        send_telegram(tg_id, "Авторизация через GitHub не удалась (нет токена).")
        return "Auth failed", 400

    user_resp = requests.get(f"{API_BASE}/user",
                             headers={"Authorization": f"Bearer {access_token}", "Accept":"application/vnd.github+json"},
                             timeout=10)
    if user_resp.status_code != 200:
        send_telegram(tg_id, "Не удалось получить данные пользователя с GitHub.")
        return "User fetch failed", 400
    user = user_resp.json()
    login = user.get("login")

    orgs_resp = requests.get(f"{API_BASE}/user/orgs",
                             headers={"Authorization": f"Bearer {access_token}", "Accept":"application/vnd.github+json"},
                             timeout=10)
    member = False
    if orgs_resp.status_code == 200:
        orgs = [o.get("login").lower() for o in orgs_resp.json()]
        if AUTH_ORG and AUTH_ORG.lower() in orgs:
            member = True

    if not member and AUTH_TEAM_SLUG:
        team_url = f"{API_BASE}/orgs/{AUTH_ORG}/teams/{AUTH_TEAM_SLUG}/memberships/{login}"
        team_resp = requests.get(team_url, headers={"Authorization": f"Bearer {access_token}", "Accept":"application/vnd.github+json"}, timeout=10)
        if team_resp.status_code == 200:
            member = True

    if member:
        save_user(tg_id, login, access_token, scopes)
        send_telegram(tg_id, f"✅ Успешно: GitHub {login} — допущен(а).")
        return "<html><body><h3>Авторизация успешна — можно закрыть окно.</h3></body></html>"
    else:
        send_telegram(tg_id, f"⚠️ GitHub {login} не найден в {AUTH_ORG}. Доступ запрещён.")
        return "<html><body><h3>Доступ запрещён — вы не в нужной организации/команде.</h3></body></html>"

def send_telegram(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)

@app.route('/is_authorized')
def is_auth():
    secret = request.headers.get('X-SERVICE-SECRET')
    if secret != SERVICE_SECRET:
        return jsonify({"error":"unauthorized"}), 403
    tg = request.args.get('telegram_id')
    if not tg:
        return jsonify({"error":"telegram_id required"}), 400
    login = is_authorized(int(tg))
    return jsonify({"authorized": bool(login), "github_login": login})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
