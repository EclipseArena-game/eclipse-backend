import os
import time
import uuid
import random
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

# ---------- BASIC APP SETUP ----------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")
CORS(app, supports_credentials=True)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="eventlet",
)

# ---------- IN-MEMORY DATA (DEV ONLY) ----------

USERS = {}  # user_id -> user dict
USERNAME_INDEX = {}  # username -> user_id
TOKENS = {}  # token -> user_id

CHARACTER_TEMPLATES = [
    {"id": "fighter_1", "name": "Blaze", "rarity": "Common", "hp": 100, "damage": 10, "speed": 5, "cost_coins": 0},
    {"id": "fighter_2", "name": "Shade", "rarity": "Rare", "hp": 90, "damage": 12, "speed": 6, "cost_coins": 500},
    {"id": "fighter_3", "name": "Nova", "rarity": "Epic", "hp": 80, "damage": 14, "speed": 7, "cost_coins": 1000},
    {"id": "fighter_4", "name": "Astra", "rarity": "Legendary", "hp": 85, "damage": 16, "speed": 6, "cost_coins": 2000},
    {"id": "fighter_5", "name": "Mythos", "rarity": "Mythic", "hp": 75, "damage": 18, "speed": 8, "cost_coins": 4000},
]

SHOP_ITEMS = [
    {"id": "shop_skin_1", "name": "Blaze Neon Skin", "type": "skin", "rarity": "Epic", "cost_amount": 500, "cost_currency": "gems"},
    {"id": "shop_trail_1", "name": "Teal Magenta Trail", "type": "trail", "rarity": "Legendary", "cost_amount": 2000, "cost_currency": "coins"},
]

MAP_TEMPLATES = [
    {"name": "Sky Platforms", "style": "Floating islands", "tagline": "Stick Fight–style floating chaos"},
    {"name": "Central Tower", "style": "Vertical climb", "tagline": "Fight up and down the tower"},
    {"name": "Breakable Floor", "style": "Destructible", "tagline": "The floor won’t last forever"},
    {"name": "Twin Bridges", "style": "Symmetrical", "tagline": "Two bridges, one winner"},
    {"name": "Pit Arena", "style": "Central hazard", "tagline": "Don’t fall into the pit"},
    {"name": "Moving Platforms", "style": "Dynamic", "tagline": "Platforms never stay still"},
    {"name": "Multi-Island", "style": "Separated", "tagline": "Jump or fall"},
    {"name": "Narrow Corridor", "style": "Tight", "tagline": "Close-quarters chaos"},
    {"name": "Open Sky", "style": "Minimal", "tagline": "Nowhere to hide"},
    {"name": "Rotating Arena", "style": "Rotating", "tagline": "The map shifts under you"},
    {"name": "Jump Pad Field", "style": "Bouncy", "tagline": "Fly across the map"},
    {"name": "Shadow Platforms", "style": "Dark", "tagline": "Low visibility, high stakes"},
    {"name": "Crystal Garden", "style": "Light", "tagline": "Bright and clean"},
    {"name": "Inferno Stage", "style": "Lava", "tagline": "Don’t touch the floor"},
    {"name": "Lunar Base", "style": "Low gravity", "tagline": "Float and fight"},
]

EVENTS = [
    # 8 dark, 4 light
    {"event_name": "Shadow Eclipse", "theme": "dark"},
    {"event_name": "Inferno Nightfall", "theme": "dark"},
    {"event_name": "Toxic Midnight", "theme": "dark"},
    {"event_name": "Lunar Rift", "theme": "dark"},
    {"event_name": "Void Surge", "theme": "dark"},
    {"event_name": "Neon Abyss", "theme": "dark"},
    {"event_name": "Obsidian Clash", "theme": "dark"},
    {"event_name": "Phantom Storm", "theme": "dark"},
    {"event_name": "Crystal Bloom", "theme": "light"},
    {"event_name": "Solar Garden", "theme": "light"},
    {"event_name": "Aurora Veil", "theme": "light"},
    {"event_name": "Prism Sky", "theme": "light"},
]

CURRENT_EVENT_INDEX = 0
CURRENT_EVENT = None

LEADERBOARDS = {
    "wins": {},
    "damage": {},
    "kos": {},
    "event_xp": {},
    "bp": {},
    "admin": {},
}

MATCHES = {}  # room_id -> match_state
QUEUE_1V1 = []
QUEUE_2V2 = []

ADMIN_EVENTS = []
SCHEDULED_EVENTS = []

# ---------- UTILS ----------

def generate_token():
  return str(uuid.uuid4())

def now_ts():
  return int(time.time())

def get_current_event():
  global CURRENT_EVENT, CURRENT_EVENT_INDEX
  if CURRENT_EVENT is None:
    CURRENT_EVENT_INDEX = 0
    base = EVENTS[CURRENT_EVENT_INDEX]
    CURRENT_EVENT = build_event_palette(base)
  return CURRENT_EVENT

def build_event_palette(base):
  name = base["event_name"]
  theme = base["theme"]
  if theme == "dark":
    palette = {
      "primary": "#2DD4D4",
      "secondary": "#D946EF",
      "highlight": "#FACC15",
      "ice": "#93C5FD",
      "background_particles": ["#2DD4D4", "#D946EF", "#93C5FD"],
    }
  else:
    palette = {
      "primary": "#0D9488",
      "secondary": "#C026D3",
      "highlight": "#CA8A04",
      "ice": "#60A5FA",
      "background_particles": ["#60A5FA", "#A21CAF", "#0D9488"],
    }
  return {
    "event_name": name,
    "theme": theme,
    "palette": palette,
  }

def get_user_from_token():
  auth = request.headers.get("Authorization", "")
  if not auth.startswith("Bearer "):
    return None
  token = auth.split(" ", 1)[1]
  user_id = TOKENS.get(token)
  if not user_id:
    return None
  return USERS.get(user_id)

def ensure_bogacactus_first(user):
  if user["username"] != "Bogacactus":
    return
  existing_admin = any(u.get("is_first_bogacactus") for u in USERS.values())
  if not existing_admin:
    user["is_first_bogacactus"] = True
  else:
    user["is_first_bogacactus"] = False

def serialize_user(user):
  return {
    "id": user["id"],
    "username": user["username"],
    "coins": user["coins"],
    "gems": user["gems"],
    "star_points": user["star_points"],
    "selected_character_id": user.get("selected_character_id"),
    "is_first_bogacactus": user.get("is_first_bogacactus", False),
  }

def build_user_characters(user):
  owned_ids = user["owned_characters"]
  chars = []
  for tmpl in CHARACTER_TEMPLATES:
    c = tmpl.copy()
    c["owned"] = c["id"] in owned_ids
    chars.append(c)
  return chars

def build_battlepass(user):
  return {
    "event_name": get_current_event()["event_name"],
    "level": user["bp_level"],
    "xp": user["bp_xp"],
    "xp_for_next": 100,
    "levels": [
      {
        "id": f"bp_{lvl}",
        "level": lvl,
        "reward_label": f"Reward {lvl}",
        "claimed": lvl <= user["bp_level"],
        "can_claim": lvl == user["bp_level"] + 1 and user["bp_xp"] >= 100,
      }
      for lvl in range(1, 11)
    ],
  }

def build_shop(user):
  return SHOP_ITEMS

def build_maps():
  return MAP_TEMPLATES

def update_leaderboard(stat, user_id, value):
  LEADERBOARDS[stat][user_id] = value

def get_leaderboard_entries(stat, limit=75):
  entries = []
  for user_id, value in LEADERBOARDS[stat].items():
    user = USERS.get(user_id)
    if not user:
      continue
    entries.append({
      "user_id": user_id,
      "username": user["username"],
      "value": value,
      "value_label": value,
    })
  entries.sort(key=lambda e: e["value"], reverse=True)
  return entries[:limit]

def get_rank(stat, user_id):
  entries = get_leaderboard_entries(stat, limit=100000)
  for idx, e in enumerate(entries):
    if e["user_id"] == user_id:
      return idx + 1, e["value"]
  return None, 0

# ---------- AUTH ROUTES ----------

@app.route("/signup", methods=["POST"])
def signup():
  data = request.get_json() or {}
  username = data.get("username", "").strip()
  password = data.get("password", "").strip()
  if not username or not password:
    return "Missing username or password", 400
  if username in USERNAME_INDEX:
    return "Username already taken", 400

  user_id = str(uuid.uuid4())
  user = {
    "id": user_id,
    "username": username,
    "password": password,  # dev only
    "coins": 1000,
    "gems": 100,
    "star_points": 0,
    "owned_characters": {"fighter_1"},
    "selected_character_id": "fighter_1",
    "wins": 0,
    "damage": 0,
    "kos": 0,
    "event_xp": 0,
    "bp_level": 1,
    "bp_xp": 0,
    "admin_events_created": 0,
    "admin_events_triggered": 0,
  }
  ensure_bogacactus_first(user)

  USERS[user_id] = user
  USERNAME_INDEX[username] = user_id

  update_leaderboard("wins", user_id, 0)
  update_leaderboard("damage", user_id, 0)
  update_leaderboard("kos", user_id, 0)
  update_leaderboard("event_xp", user_id, 0)
  update_leaderboard("bp", user_id, user["bp_level"])
  if user.get("is_first_bogacactus"):
    update_leaderboard("admin", user_id, 0)

  token = generate_token()
  TOKENS[token] = user_id

  return jsonify({"token": token, "user": serialize_user(user)})

@app.route("/login", methods=["POST"])
def login():
  data = request.get_json() or {}
  username = data.get("username", "").strip()
  password = data.get("password", "").strip()
  if not username or not password:
    return "Missing username or password", 400
  user_id = USERNAME_INDEX.get(username)
  if not user_id:
    return "Invalid credentials", 400
  user = USERS[user_id]
  if user["password"] != password:
    return "Invalid credentials", 400

  ensure_bogacactus_first(user)

  token = generate_token()
  TOKENS[token] = user_id
  return jsonify({"token": token, "user": serialize_user(user)})

@app.route("/me", methods=["GET"])
def me():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401

  event = get_current_event()
  chars = build_user_characters(user)
  bp = build_battlepass(user)
  shop = build_shop(user)
  maps = build_maps()

  return jsonify({
    "user": serialize_user(user),
    "event": event,
    "characters": chars,
    "battlepass": bp,
    "shop": shop,
    "maps": maps,
  })

# ---------- CHARACTER ROUTES ----------

@app.route("/character/select", methods=["POST"])
def character_select():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  data = request.get_json() or {}
  cid = data.get("character_id")
  if cid not in user["owned_characters"]:
    return "Character not owned", 400
  user["selected_character_id"] = cid
  return jsonify({"ok": True})

@app.route("/character/unlock", methods=["POST"])
def character_unlock():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  data = request.get_json() or {}
  cid = data.get("character_id")
  tmpl = next((c for c in CHARACTER_TEMPLATES if c["id"] == cid), None)
  if not tmpl:
    return "Invalid character", 400
  if cid in user["owned_characters"]:
    return "Already owned", 400
  cost = tmpl["cost_coins"]
  if user["coins"] < cost:
    return "Not enough coins", 400
  user["coins"] -= cost
  user["owned_characters"].add(cid)
  chars = build_user_characters(user)
  return jsonify({
    "coins": user["coins"],
    "characters": chars,
  })

# ---------- SHOP ROUTES ----------

@app.route("/shop/buy", methods=["POST"])
def shop_buy():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  data = request.get_json() or {}
  item_id = data.get("item_id")
  item = next((i for i in SHOP_ITEMS if i["id"] == item_id), None)
  if not item:
    return "Invalid item", 400

  currency = item["cost_currency"]
  amount = item["cost_amount"]
  if currency == "coins":
    if user["coins"] < amount:
      return "Not enough coins", 400
    user["coins"] -= amount
  elif currency == "gems":
    if user["gems"] < amount:
      return "Not enough gems", 400
    user["gems"] -= amount

  chars = build_user_characters(user)
  return jsonify({
    "coins": user["coins"],
    "gems": user["gems"],
    "star_points": user["star_points"],
    "characters": chars,
  })

# ---------- BATTLE PASS ROUTES ----------

@app.route("/battlepass/claim", methods=["POST"])
def battlepass_claim():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  data = request.get_json() or {}
  level_id = data.get("level_id")
  try:
    lvl_num = int(level_id.split("_")[1])
  except Exception:
    lvl_num = None
  if lvl_num is None:
    return "Invalid level", 400

  if lvl_num != user["bp_level"] + 1:
    return "Not claimable", 400
  if user["bp_xp"] < 100:
    return "Not enough BP XP", 400

  user["bp_level"] += 1
  user["bp_xp"] = 0
  user["coins"] += 100 * lvl_num

  update_leaderboard("bp", user["id"], user["bp_level"])

  bp = build_battlepass(user)
  chars = build_user_characters(user)
  return jsonify({
    "coins": user["coins"],
    "gems": user["gems"],
    "star_points": user["star_points"],
    "characters": chars,
    "battlepass": bp,
  })

# ---------- LEADERBOARD ROUTES ----------

@app.route("/leaderboard/<stat>", methods=["GET"])
def leaderboard(stat):
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  if stat not in LEADERBOARDS:
    return "Invalid stat", 400
  limit = int(request.args.get("limit", 75))
  entries = get_leaderboard_entries(stat, limit=limit)
  return jsonify({"entries": entries})

@app.route("/leaderboard/rank", methods=["GET"])
def leaderboard_rank():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  stat = request.args.get("stat", "wins")
  if stat not in LEADERBOARDS:
    return "Invalid stat", 400
  rank, value = get_rank(stat, user["id"])
  if rank is None:
    return jsonify({"rank": 999999, "value": 0, "in_top_75": False})
  in_top_75 = rank <= 75
  return jsonify({"rank": rank, "value": value, "in_top_75": in_top_75})

# ---------- ADMIN ROUTES ----------

def is_admin_user(user):
  return user.get("is_first_bogacactus", False)

@app.route("/admin/event/trigger", methods=["POST"])
def admin_trigger():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  if not is_admin_user(user):
    return "Forbidden", 403
  data = request.get_json() or {}
  preset = data.get("preset")
  user["admin_events_triggered"] += 1
  update_leaderboard("admin", user["id"], user["admin_events_triggered"])
  ADMIN_EVENTS.append({
    "type": "preset",
    "preset": preset,
    "by": user["username"],
    "ts": now_ts(),
  })
  socketio.emit("event_update", get_current_event(), broadcast=True)
  return jsonify({"ok": True})

@app.route("/admin/event/schedule", methods=["POST"])
def admin_schedule():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  if not is_admin_user(user):
    return "Forbidden", 403
  data = request.get_json() or {}
  name = data.get("name")
  theme = data.get("theme", "dark")
  start = data.get("start")
  end = data.get("end")
  SCHEDULED_EVENTS.append({
    "name": name,
    "theme": theme,
    "start": start,
    "end": end,
    "by": user["username"],
  })
  user["admin_events_created"] += 1
  update_leaderboard("admin", user["id"], user["admin_events_triggered"])
  return jsonify({"ok": True})

@app.route("/admin/event/custom", methods=["POST"])
def admin_custom():
  user = get_user_from_token()
  if not user:
    return "Unauthorized", 401
  if not is_admin_user(user):
    return "Forbidden", 403
  data = request.get_json() or {}
  config = data.get("config", {})
  ADMIN_EVENTS.append({
    "type": "custom",
    "config": config,
    "by": user["username"],
    "ts": now_ts(),
  })
  user["admin_events_created"] += 1
  update_leaderboard("admin", user["id"], user["admin_events_triggered"])
  return jsonify({"ok": True})

# ---------- MATCHMAKING & MATCH STATE ----------

def build_initial_map():
  platforms = []
  for _ in range(random.randint(3, 6)):
    platforms.append({
      "x": random.uniform(0.1, 0.7),
      "y": random.uniform(0.2, 0.8),
      "w": random.uniform(0.2, 0.4),
      "h": random.uniform(0.03, 0.06),
    })
  hazards = []
  if random.random() < 0.4:
    hazards.append({
      "x": 0.5,
      "y": 0.9,
      "r": 0.15,
    })
  return {"platforms": platforms, "hazards": hazards}

def build_player_state(user, is_me=False, team=1):
  selected_id = user.get("selected_character_id", "fighter_1")
  tmpl = next((c for c in CHARACTER_TEMPLATES if c["id"] == selected_id), CHARACTER_TEMPLATES[0])
  return {
    "user_id": user["id"],
    "username": user["username"],
    "fighter": tmpl["name"],
    "rarity": tmpl["rarity"],
    "hp": tmpl["hp"],
    "max_hp": tmpl["hp"],
    "damage": tmpl["damage"],
    "speed": tmpl["speed"],
    "team": team,
    "eclipse_meter": 0,
    "block_stamina": 100,
    "blocking": False,
    "rounds_won": 0,
    "is_me": is_me,
    "moving": False,
    "screen_pos": {"x": 0.5, "y": 0.5},
  }

def create_match(room_id, players):
  event = get_current_event()
  map_state = build_initial_map()
  match = {
    "room_id": room_id,
    "players": players,
    "map": map_state,
    "active_pickups": [],
    "finished": False,
    "winning_team": None,
  }
  MATCHES[room_id] = match
  return match

def queue_player(user, mode, sid):
  if mode == "1v1":
    QUEUE_1V1.append((user, sid))
    if len(QUEUE_1V1) >= 2:
      (u1, s1), (u2, s2) = QUEUE_1V1[:2]
      del QUEUE_1V1[:2]
      start_1v1(u1, s1, u2, s2)
  elif mode == "2v2":
    QUEUE_2V2.append((user, sid))
    if len(QUEUE_2V2) >= 4:
      players = QUEUE_2V2[:4]
      del QUEUE_2V2[:4]
      start_2v2(players)

def start_1v1(u1, s1, u2, s2):
  room_id = str(uuid.uuid4())
  join_room(room_id, sid=s1)
  join_room(room_id, sid=s2)
  p1 = build_player_state(u1, is_me=False, team=1)
  p2 = build_player_state(u2, is_me=False, team=2)
  match = create_match(room_id, {
    s1: p1,
    s2: p2,
  })
  socketio.emit("match_start", match, room=room_id)

def start_2v2(players):
  room_id = str(uuid.uuid4())
  players_state = {}
  teams = [1, 1, 2, 2]
  random.shuffle(teams)
  for (user, sid), team in zip(players, teams):
    join_room(room_id, sid=sid)
    players_state[sid] = build_player_state(user, is_me=False, team=team)
  match = create_match(room_id, players_state)
  socketio.emit("match_start", match, room=room_id)

def apply_action(room_id, sid, action):
  match = MATCHES.get(room_id)
  if not match or match["finished"]:
    return
  players = match["players"]
  if sid not in players:
    return
  p = players[sid]

  if action == "LIGHT_ATTACK":
    for oid, op in players.items():
      if oid != sid and op["team"] != p["team"]:
        op["hp"] = max(0, op["hp"] - p["damage"])
        p["eclipse_meter"] = min(100, p["eclipse_meter"] + 5)
        p["moving"] = True
  elif action == "HEAVY_ATTACK":
    for oid, op in players.items():
      if oid != sid and op["team"] != p["team"]:
        op["hp"] = max(0, op["hp"] - int(p["damage"] * 1.5))
        p["eclipse_meter"] = min(100, p["eclipse_meter"] + 10)
        p["moving"] = True
  elif action == "BLOCK":
    p["blocking"] = True
    p["block_stamina"] = max(0, p["block_stamina"] - 5)
  elif action == "ABILITY":
    p["moving"] = True
  elif action == "ECLIPSE":
    if p["eclipse_meter"] >= 100:
      for oid, op in players.items():
        if oid != sid and op["team"] != p["team"]:
          op["hp"] = max(0, op["hp"] - p["damage"] * 2)
      p["eclipse_meter"] = 0
      p["moving"] = True

  for oid, op in players.items():
    if op["hp"] <= 0:
      match["finished"] = True
      match["winning_team"] = p["team"]
      break

  socketio.emit("state_update", match, room=room_id)

# ---------- SOCKET.IO HANDLERS ----------

@socketio.on("connect")
def on_connect():
  pass

@socketio.on("disconnect")
def on_disconnect():
  pass

@socketio.on("queue_1v1")
def on_queue_1v1():
  token = request.headers.get("Authorization", "").replace("Bearer ", "")
  user_id = TOKENS.get(token)
  if not user_id:
    return
  user = USERS.get(user_id)
  if not user:
    return
  queue_player(user, "1v1", request.sid)

@socketio.on("queue_2v2")
def on_queue_2v2():
  token = request.headers.get("Authorization", "").replace("Bearer ", "")
  user_id = TOKENS.get(token)
  if not user_id:
    return
  user = USERS.get(user_id)
  if not user:
    return
  queue_player(user, "2v2", request.sid)

@socketio.on("action")
def on_action(data):
  room_ids = list(socketio.server.rooms(request.sid))
  room_id = None
  for r in room_ids:
    if r != request.sid:
      room_id = r
      break
  if not room_id:
    return
  action = data.get("action")
  apply_action(room_id, request.sid, action)

# ---------- MAIN ----------

if __name__ == "__main__":
  CURRENT_EVENT = get_current_event()
  socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
