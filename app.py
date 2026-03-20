"""
AevaOS Mission Control API v1.5.0
v1.5.0 — ACP multi-agent mesh: triage dispatch, per-agent model routing, self-learning feedback
v1.4.0 — PostgreSQL migration via SQLAlchemy (dual-mode: DB or JSON fallback)
v1.3.0 — Daily briefing, SSE activity stream, task detail, unified search, blockers write
v1.2.0 — Smart alerts, analytics summary, POST tasks, task filtering
v1.1.0 — Write endpoints (PATCH tasks, POST ideas, POST activity, PATCH agents)
"""

from flask import Flask, jsonify, request, abort, Response, stream_with_context
from flask_cors import CORS
import json
import os
import datetime
import time

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# Fix Railway's postgres:// prefix for SQLAlchemy 2.x
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

if _db_url:
    app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }
    from models import db
    db.init_app(app)
    print("Using PostgreSQL storage")
else:
    print("Using JSON file storage (DATABASE_URL not set)")

# ── Storage layer (auto-selects DB or JSON) ───────────────────────────────────
from storage import (
    storage_get_tasks, storage_get_task, storage_save_task, storage_delete_task,
    storage_get_ideas, storage_save_idea, storage_next_idea_id,
    storage_get_projects,
    storage_get_agents, storage_save_agent,
    storage_append_activity, storage_get_activity, storage_activity_count,
    storage_get_activity_since_count,
    storage_get_messages, storage_save_message,
    storage_get_blockers, storage_save_blocker,
    storage_get_credits,
    storage_log_dispatch, storage_get_dispatches, storage_add_feedback,
)
from triage import dispatch as triage_dispatch

# ── JSON helpers (still used for credits + briefing where no DB model) ────────
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _data_path(filename):
    return os.path.join(DATA_DIR, filename)


def read_json(filename):
    path = _data_path(filename)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def write_json(filename, data):
    with open(_data_path(filename), "w") as f:
        json.dump(data, f, indent=2)


def now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── DB initialisation (create tables + seed on first boot with DB) ────────────
def init_db_and_seed():
    if not _db_url:
        return
    from models import db, Task, Idea, Project, Agent
    with app.app_context():
        db.create_all()
        # Only seed if all tables are empty
        if Task.query.count() == 0:
            try:
                import seed_db
                seed_db.seed_tasks()
                seed_db.seed_ideas()
                seed_db.seed_projects()
                seed_db.seed_agents()
                seed_db.seed_activity()
                seed_db.seed_messages()
                seed_db.seed_blockers()
                print("Auto-seed from JSON completed.")
            except Exception as e:
                print(f"Auto-seed skipped or failed: {e}")


# Run seed at module load (gunicorn imports app once per worker)
with app.app_context():
    if _db_url:
        try:
            from models import db
            db.create_all()
            from models import Task
            if Task.query.count() == 0:
                import seed_db
                seed_db.seed_tasks()
                seed_db.seed_ideas()
                seed_db.seed_projects()
                seed_db.seed_agents()
                seed_db.seed_activity()
                seed_db.seed_messages()
                seed_db.seed_blockers()
                print("✅ Auto-seed complete")
        except Exception as exc:
            print(f"⚠️  DB init/seed error (non-fatal): {exc}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "aevaos-api", "version": "1.5.0",
                    "storage": "postgresql" if _db_url else "json"})


# ---------------------------------------------------------------------------
# Auth — simple password-based session tokens
# ---------------------------------------------------------------------------
import hmac
import hashlib
import base64

def _secret_key() -> str:
    """Return the HMAC signing secret — falls back to a build-time constant if not set."""
    return os.environ.get("AUTH_SECRET_KEY", "aevaos-default-insecure-key-change-me")

def _make_token(payload: str) -> str:
    """HMAC-SHA256 sign a payload string, return base64url token."""
    sig = hmac.new(_secret_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}.{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()

def _verify_token(token: str) -> bool:
    """Returns True if the token signature is valid and not expired."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        payload, sig = raw.rsplit(".", 1)
        expected = hmac.new(_secret_key().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        # Payload is "aevaos:<issued_ts>"
        issued_at = int(payload.split(":")[1])
        if time.time() - issued_at > 7 * 24 * 3600:  # 7-day expiry
            return False
        return True
    except Exception:
        return False


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """
    POST /api/auth/login  { password }
    Validates against MISSION_CONTROL_PASSWORD env var.
    Returns { token } on success.
    """
    body = request.get_json() or {}
    password = body.get("password", "")
    stored = os.environ.get("MISSION_CONTROL_PASSWORD", "")

    if not stored:
        return jsonify({"error": "Auth not configured — set MISSION_CONTROL_PASSWORD on Railway"}), 503

    if not hmac.compare_digest(password, stored):
        return jsonify({"error": "Invalid password"}), 401

    payload = f"aevaos:{int(time.time())}"
    token = _make_token(payload)
    return jsonify({"token": token, "expires_in": 7 * 24 * 3600})


@app.route("/api/auth/verify", methods=["POST"])
def auth_verify():
    """
    POST /api/auth/verify  (Authorization: Bearer <token>)
    Returns 200 if token is valid, 401 otherwise.
    """
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if _verify_token(token):
        return jsonify({"valid": True})
    return jsonify({"valid": False, "error": "Invalid or expired token"}), 401




@app.route("/api/health/db", methods=["GET"])
def health_db():
    """DB health + table counts for verification."""
    if not _db_url:
        return jsonify({"storage": "json", "message": "DATABASE_URL not set"})
    try:
        from models import Task, Idea, Project, Agent, ActivityEntry, MeetingMessage, BlockerScan
        return jsonify({
            "storage": "postgresql",
            "tables": ["tasks", "ideas", "projects", "agents", "activity_feed", "meeting_messages", "blocker_scans"],
            "counts": {
                "tasks": Task.query.count(),
                "ideas": Idea.query.count(),
                "projects": Project.query.count(),
                "agents": Agent.query.count(),
                "activity_feed": ActivityEntry.query.count(),
                "meeting_messages": MeetingMessage.query.count(),
                "blocker_scans": BlockerScan.query.count(),
            }
        })
    except Exception as e:
        return jsonify({"storage": "postgresql", "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Office routes
# ---------------------------------------------------------------------------

@app.route("/api/office/agents", methods=["GET"])
def get_agents():
    return jsonify(storage_get_agents())


@app.route("/api/office/agents/<agent_id>", methods=["PATCH"])
def update_agent(agent_id):
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    result = storage_save_agent(agent_id, data)
    if not result:
        abort(404, description=f"Agent '{agent_id}' not found")
    return jsonify(result)


@app.route("/api/office/activity", methods=["GET"])
def get_activity():
    limit        = request.args.get("limit", 50, type=int)
    agent_filter = request.args.get("agent")
    since        = request.args.get("since")
    return jsonify(storage_get_activity(limit=limit, agent_filter=agent_filter, since=since))


@app.route("/api/office/activity", methods=["POST"])
def post_activity():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    entry = storage_append_activity(
        agent=data.get("agent", "unknown"),
        action=data.get("action", "note"),
        message=data.get("message", ""),
        metadata=data.get("metadata", {}),
    )
    return jsonify(entry), 201


# ---------------------------------------------------------------------------
# ACP Dispatch — multi-agent mesh
# ---------------------------------------------------------------------------

@app.route("/api/agents/dispatch", methods=["POST"])
def agent_dispatch():
    """
    POST /api/agents/dispatch
    Body: { message, context?, thread_id? }

    API routing:
      - Clara  → OpenAI direct (OPENAI_API_KEY)  — codex-mini-latest
      - Others → OpenRouter (OPENROUTER_API_KEY) — claude/gemini/etc.
      - Fallback when OpenRouter out of credits → Anthropic direct (ANTHROPIC_API_KEY)
    """
    body = request.get_json()
    if not body or not body.get("message"):
        abort(400, description="'message' is required")

    message   = body["message"]
    context   = body.get("context", {})
    thread_id = body.get("thread_id")

    response = triage_dispatch(
        message=message,
        context=context,
        thread_id=thread_id,
        openrouter_key=os.environ.get("OPENROUTER_API_KEY", ""),
        openai_key=os.environ.get("OPENAI_API_KEY", ""),
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    )

    # Enrich with input for logging
    from dataclasses import asdict
    resp_dict = asdict(response)
    resp_dict["input_message"] = message
    resp_dict["context_snapshot"] = context

    try:
        storage_log_dispatch(resp_dict)
    except Exception as log_err:
        print(f"[dispatch] log error: {log_err}")

    storage_append_activity(
        agent=response.agent,
        action="dispatch",
        message=f"Dispatched '{message[:60]}...' → {response.agent} ({response.model})",
        metadata={
            "dispatch_id": response.dispatch_id,
            "classification": resp_dict.get("classification"),
            "latency_ms": response.latency_ms,
            "status": response.status,
        }
    )

    return jsonify(resp_dict)


@app.route("/api/agents/dispatch/history", methods=["GET"])
def dispatch_history():
    """
    GET /api/agents/dispatch/history?limit=50&agent=clara
    Returns recent dispatch log entries.
    """
    limit = request.args.get("limit", 50, type=int)
    agent = request.args.get("agent")
    dispatches = storage_get_dispatches(limit=limit, agent=agent)
    return jsonify({"dispatches": dispatches, "count": len(dispatches)})


@app.route("/api/agents/feedback", methods=["POST"])
def agent_feedback():
    """
    POST /api/agents/feedback
    Body: { dispatch_id, rating (1-5), note?, routing_correct? }
    Used by Aeva for self-learning from response quality.
    """
    body = request.get_json()
    if not body or not body.get("dispatch_id") or not body.get("rating"):
        abort(400, description="'dispatch_id' and 'rating' are required")

    result = storage_add_feedback(
        dispatch_id=body["dispatch_id"],
        rating=int(body["rating"]),
        note=body.get("note"),
        routing_correct=body.get("routing_correct"),
    )

    storage_append_activity(
        agent="system",
        action="feedback_logged",
        message=f"Dispatch {body['dispatch_id'][:8]}... rated {body['rating']}/5",
        metadata={"dispatch_id": body["dispatch_id"], "rating": body["rating"]},
    )
    return jsonify(result)


# ---------------------------------------------------------------------------
# SSE Activity Stream
# ---------------------------------------------------------------------------

@app.route("/api/stream/activity", methods=["GET"])
def stream_activity():
    def generate():
        last_count = storage_activity_count()
        yield "event: heartbeat\ndata: {\"ts\": \"" + now_iso() + "\"}\n\n"
        while True:
            time.sleep(2)
            new_entries = storage_get_activity_since_count(last_count)
            if new_entries:
                for entry in new_entries:
                    yield f"data: {json.dumps(entry)}\n\n"
                last_count += len(new_entries)
            else:
                yield "event: heartbeat\ndata: {\"ts\": \"" + now_iso() + "\"}\n\n"

    resp = Response(stream_with_context(generate()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ---------------------------------------------------------------------------
# Meeting room
# ---------------------------------------------------------------------------

@app.route("/api/office/meeting-room", methods=["GET"])
def get_meeting_room():
    room = read_json("meeting-room.json")
    if not room:
        return jsonify({"rooms": {}, "metadata": {"totalRooms": 0}})
    return jsonify(room)


@app.route("/api/office/meeting-room/<room_id>", methods=["GET"])
def get_meeting_transcript(room_id):
    limit = request.args.get("limit", 50, type=int)
    return jsonify(storage_get_messages(room_id, limit))


@app.route("/api/office/message", methods=["POST"])
def post_message():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    room_id = data.get("room_id", "main-office")
    msg = {
        "timestamp": data.get("timestamp", now_iso()),
        "from":      data.get("from", "user"),
        "to":        data.get("to", "all"),
        "message":   data.get("message", ""),
        "type":      data.get("type", "message"),
    }
    result = storage_save_message(room_id, msg)
    storage_append_activity(
        agent=msg["from"], action="message",
        message=f"[{room_id}] {msg['message'][:80]}",
        metadata={"room": room_id},
    )
    return jsonify(result), 201


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

@app.route("/api/credits", methods=["GET"])
def get_credits():
    credits = storage_get_credits()
    if not credits:
        return jsonify({"providers": {}, "lastChecked": None})
    return jsonify(credits)


@app.route("/api/credits/refresh", methods=["POST"])
def refresh_credits():
    """
    Fetch live OpenRouter balance and update credit-status.json.
    Requires OPENROUTER_API_KEY env var.
    """
    import urllib.request
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OPENROUTER_API_KEY not set"}), 503

    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        balance = data.get("data", {}).get("limit_remaining", None)
        usage   = data.get("data", {}).get("usage", None)
        limit   = data.get("data", {}).get("limit", None)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    credits = read_json("credit-status.json") or {"providers": {}}
    providers = credits.get("providers", {})
    if "openrouter" not in providers:
        providers["openrouter"] = {}
    if balance is not None:
        providers["openrouter"]["balance"] = round(float(balance), 4)
    if usage is not None:
        providers["openrouter"]["usage"] = round(float(usage), 4)
    if limit is not None:
        providers["openrouter"]["limit"] = round(float(limit), 4)
    providers["openrouter"]["status"] = "active"
    credits["providers"] = providers
    credits["lastChecked"] = now_iso()
    write_json("credit-status.json", credits)

    storage_append_activity(
        agent="system", action="credits_refreshed",
        message=f"OpenRouter balance refreshed: ${balance:.2f}" if balance else "Credits refreshed",
        metadata={"provider": "openrouter", "balance": balance},
    )
    return jsonify({"success": True, "balance": balance, "lastChecked": credits["lastChecked"]})


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    status_filter   = request.args.get("status")
    assignee_filter = request.args.get("assignee")
    priority_filter = request.args.get("priority")
    project_filter  = request.args.get("project")
    return jsonify(storage_get_tasks(
        status=status_filter, assignee=assignee_filter,
        priority=priority_filter, project=project_filter,
    ))


@app.route("/api/tasks", methods=["POST"])
def create_task():
    data = request.get_json()
    if not data or not data.get("title"):
        abort(400, description="'title' is required")

    # Generate next ID
    all_tasks = storage_get_tasks()
    tasks_list = all_tasks.get("tasks", [])
    existing_ids = [t["id"] for t in tasks_list if t["id"].startswith("TASK-")]
    max_num = max(
        (int(tid.replace("TASK-", "")) for tid in existing_ids if tid.replace("TASK-", "").isdigit()),
        default=0
    )
    next_id = f"TASK-{max_num + 1:03d}"

    task = {
        "id":           next_id,
        "title":        data["title"],
        "description":  data.get("description", ""),
        "project":      data.get("project", "aeva-os"),
        "status":       data.get("status", "ready"),
        "assignee":     data.get("assignee", ""),
        "urgency":      data.get("urgency", "medium"),
        "complexity":   data.get("complexity", "medium"),
        "priority":     data.get("priority", "medium"),
        "is_blocked":   False,
        "days_stuck":   0,
        "createdAt":    now_iso(),
        "createdBy":    data.get("createdBy", "ui"),
        "last_updated": now_iso(),
        "activity_log": [{"timestamp": now_iso(), "status": "ready", "note": "Task created"}],
        "notes":        [],
    }
    result = storage_save_task(task)
    storage_append_activity(
        agent=data.get("createdBy", "ui"), action="task_created",
        message=f"New task: {task['title']}", metadata={"taskId": next_id},
    )
    return jsonify(result), 201


@app.route("/api/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    task = storage_get_task(task_id)
    if not task:
        abort(404, description=f"Task '{task_id}' not found")
    return jsonify(task)


@app.route("/api/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id):
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")

    task = storage_get_task(task_id)
    if not task:
        abort(404, description=f"Task '{task_id}' not found")

    old_status = task.get("status")

    # Apply allowed field updates
    for key in ("status", "assignee", "priority", "urgency", "is_blocked", "title", "description"):
        if key in data:
            task[key] = data[key]
    task["last_updated"] = now_iso()

    # Status change → append to activity_log + set timestamps
    if "status" in data:
        task.setdefault("activity_log", []).append({
            "timestamp": now_iso(),
            "status":    data["status"],
            "note":      data.get("note", f"Status changed {old_status} → {data['status']}"),
        })
        if data["status"] == "done" and not task.get("completedAt"):
            task["completedAt"] = now_iso()
        if data["status"] == "in-progress" and not task.get("startedAt"):
            task["startedAt"] = now_iso()
        storage_append_activity(
            agent=data.get("updatedBy", "ui"),
            action=f"task_{data['status'].replace('-', '_')}",
            message=f"{task['title']} → {data['status']}",
            metadata={"taskId": task_id},
        )

    # Note added without status change
    if "note" in data and "status" not in data:
        task.setdefault("activity_log", []).append({
            "timestamp": now_iso(), "status": task.get("status", ""), "note": data["note"],
        })

    result = storage_save_task(task)
    return jsonify(result)


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    found = storage_delete_task(task_id)
    if not found:
        abort(404, description=f"Task '{task_id}' not found")
    storage_append_activity(
        agent="ui", action="task_deleted",
        message=f"Task {task_id} deleted", metadata={"taskId": task_id},
    )
    return jsonify({"deleted": task_id})


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.route("/api/projects", methods=["GET"])
def get_projects():
    return jsonify(storage_get_projects())


# ---------------------------------------------------------------------------
# Project READMEs — stored as flat markdown files in data/project-readmes/
# ---------------------------------------------------------------------------

READMES_DIR = os.path.join(DATA_DIR, "project-readmes")
os.makedirs(READMES_DIR, exist_ok=True)


@app.route("/api/projects/<project_id>/readme", methods=["GET"])
def get_project_readme(project_id):
    safe_id = "".join(c for c in project_id if c.isalnum() or c in "-_")
    path = os.path.join(READMES_DIR, f"{safe_id}.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return jsonify({"content": f.read(), "exists": True})
    return jsonify({"content": "", "exists": False})


@app.route("/api/projects/<project_id>/readme", methods=["PUT"])
def put_project_readme(project_id):
    safe_id = "".join(c for c in project_id if c.isalnum() or c in "-_")
    body = request.get_json() or {}
    content = body.get("content", "")
    path = os.path.join(READMES_DIR, f"{safe_id}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return jsonify({"saved": True, "size": len(content)})


# ---------------------------------------------------------------------------
# Ideas
# ---------------------------------------------------------------------------

@app.route("/api/ideas", methods=["GET"])
def get_ideas():
    return jsonify(storage_get_ideas())


@app.route("/api/ideas", methods=["POST"])
def post_idea():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    next_id = storage_next_idea_id()
    idea = {
        "id":          next_id,
        "title":       data.get("title", "Untitled"),
        "description": data.get("description", ""),
        "category":    data.get("category", "research"),
        "source":      data.get("source", "api"),
        "capturedAt":  now_iso(),
        "status":      "new",
        "tags":        data.get("tags", []),
    }
    result = storage_save_idea(idea)
    storage_append_activity(
        agent=data.get("source", "api"), action="idea_captured",
        message=f"New idea: {idea['title']}", metadata={"ideaId": idea["id"]},
    )
    return jsonify(result), 201


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------

@app.route("/api/blockers", methods=["GET"])
def get_blockers():
    return jsonify(storage_get_blockers())


@app.route("/api/blockers", methods=["POST"])
def post_blocker():
    data = request.get_json() or {}
    entry = {
        "detected_at": data.get("detected_at", now_iso()),
        "blockers":    data.get("blockers", []),
        "source":      data.get("source", "manual"),
    }
    result = storage_save_blocker(entry)
    return jsonify(result), 201


# ---------------------------------------------------------------------------
# Unified Search
# ---------------------------------------------------------------------------

@app.route("/api/search", methods=["GET"])
def unified_search():
    query = (request.args.get("q") or "").lower().strip()
    if not query or len(query) < 2:
        return jsonify({"results": [], "query": query, "count": 0})

    results = []

    for t in storage_get_tasks().get("tasks", []):
        text = f"{t.get('title','')} {t.get('description','')} {t.get('project','')} {t.get('assignee','')}".lower()
        if query in text:
            score = 10 if query in t.get("title", "").lower() else 0
            score += text.count(query) * 2
            results.append({
                "type": "task", "id": t["id"], "title": t["title"],
                "description": t.get("description", "")[:120],
                "meta": {"status": t.get("status"), "assignee": t.get("assignee"), "project": t.get("project")},
                "score": score, "href": "/tasks",
            })

    for idea in storage_get_ideas().get("ideas", []):
        text = f"{idea.get('title','')} {idea.get('description','')} {' '.join(idea.get('tags', []))}".lower()
        if query in text:
            results.append({
                "type": "idea", "id": idea["id"], "title": idea["title"],
                "description": idea.get("description", "")[:120],
                "meta": {"category": idea.get("category"), "status": idea.get("status")},
                "score": 10 if query in idea.get("title", "").lower() else 5,
                "href": "/ideas",
            })

    for p in storage_get_projects().get("projects", []):
        text = f"{p.get('name','')} {p.get('description','')} {p.get('id','')}".lower()
        if query in text:
            results.append({
                "type": "project", "id": p["id"], "title": p.get("name", p["id"]),
                "description": p.get("description", "")[:120],
                "meta": {"status": p.get("status"), "health": p.get("health")},
                "score": 15 if query in p.get("name", "").lower() else 7,
                "href": "/projects",
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return jsonify({"results": results[:20], "query": query, "count": len(results)})


# ---------------------------------------------------------------------------
# Smart Alerts
# ---------------------------------------------------------------------------

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    alerts = []
    now = datetime.datetime.utcnow()

    for task in storage_get_tasks().get("tasks", []):
        if task.get("is_blocked"):
            alerts.append({
                "id": f"blocked-{task['id']}", "level": "warning", "type": "task_blocked",
                "title": f"Task blocked: {task['title']}", "message": f"{task['id']} is marked as blocked",
                "taskId": task["id"], "timestamp": task.get("last_updated", now_iso()),
            })
        if task.get("status") == "in-progress":
            last = task.get("last_updated") or task.get("startedAt")
            if last:
                try:
                    last_dt = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                    hours_stale = (now - last_dt).total_seconds() / 3600
                    if hours_stale > 48:
                        alerts.append({
                            "id": f"stale-{task['id']}", "level": "warning", "type": "task_stale",
                            "title": f"Stale task: {task['title']}",
                            "message": f"{task['id']} in-progress for {int(hours_stale)}h without update",
                            "taskId": task["id"], "hoursStale": int(hours_stale), "timestamp": now_iso(),
                        })
                except ValueError:
                    pass

    credits = storage_get_credits()
    for provider, data in credits.get("providers", {}).items():
        status = data.get("status", "unknown")
        if status in ("unlimited", "unknown"):
            continue
        balance = data.get("balance")
        usage   = data.get("usage")
        limit   = data.get("limit")
        budget_thresholds = credits.get("budget", {}).get("alertThresholds", {}).get(provider, {})
        warn_at     = budget_thresholds.get("warn", 20)
        critical_at = budget_thresholds.get("critical", 10)

        if balance is not None:
            if balance <= critical_at:
                level = "critical"
            elif balance <= warn_at:
                level = "warning"
            else:
                level = None

            if level:
                alerts.append({
                    "id": f"credit-{provider}", "level": level, "type": "credit_low",
                    "title": f"{'Critical' if level == 'critical' else 'Low'} balance: {provider}",
                    "message": f"${balance:.2f} remaining (warn at ${warn_at})",
                    "provider": provider, "timestamp": now_iso(),
                })
            continue

        threshold = data.get("alert_threshold", 80)
        if limit and limit > 0 and usage is not None:
            pct = (usage / limit) * 100
            if pct >= threshold:
                level = "critical" if pct >= 95 else "warning"
                alerts.append({
                    "id": f"credit-{provider}", "level": level, "type": "credit_low",
                    "title": f"{'Critical' if level == 'critical' else 'Low'} credits: {provider}",
                    "message": f"{provider} at {pct:.0f}% of limit",
                    "provider": provider, "timestamp": now_iso(),
                })

    alerts.sort(key=lambda a: 0 if a["level"] == "critical" else 1)
    return jsonify({
        "alerts": alerts, "count": len(alerts),
        "critical": len([a for a in alerts if a["level"] == "critical"]),
        "warnings": len([a for a in alerts if a["level"] == "warning"]),
        "generatedAt": now_iso(),
    })


# ---------------------------------------------------------------------------
# Analytics Summary
# ---------------------------------------------------------------------------

@app.route("/api/analytics/summary", methods=["GET"])
def get_analytics_summary():
    now = datetime.datetime.utcnow()
    seven_days_ago = now - datetime.timedelta(days=7)

    tasks        = storage_get_tasks().get("tasks", [])
    done_tasks   = [t for t in tasks if t.get("status") == "done"]
    in_progress  = [t for t in tasks if t.get("status") == "in-progress"]
    blocked      = [t for t in tasks if t.get("is_blocked") or t.get("status") == "blocked"]
    ready        = [t for t in tasks if t.get("status") == "ready"]

    velocity_done = []
    for t in done_tasks:
        ca = t.get("completedAt")
        if ca:
            try:
                if datetime.datetime.strptime(ca, "%Y-%m-%dT%H:%M:%SZ") >= seven_days_ago:
                    velocity_done.append(t)
            except ValueError:
                pass

    completion_rate = round(len(done_tasks) / len(tasks) * 100 if tasks else 0, 1)
    assignee_counts: dict = {}
    for t in tasks:
        a = t.get("assignee")
        if a:
            assignee_counts[a] = assignee_counts.get(a, 0) + 1
    top_contributors = sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True)

    activity      = storage_get_activity(limit=200)
    recent_activity = [e for e in activity if _is_recent(e.get("timestamp", ""), seven_days_ago)]

    agents_data   = storage_get_agents()
    active_agents = [k for k, v in agents_data.get("agents", {}).items() if v.get("status") in ("active", "busy")]

    project_counts: dict = {}
    for t in tasks:
        p = t.get("project", "unassigned")
        project_counts[p] = project_counts.get(p, 0) + 1

    return jsonify({
        "generatedAt": now_iso(),
        "tasks": {
            "total": len(tasks), "done": len(done_tasks), "inProgress": len(in_progress),
            "blocked": len(blocked), "ready": len(ready), "completionRate": completion_rate,
            "velocity7d": len(velocity_done),
            "recentCompletions": [{"id": t["id"], "title": t["title"], "completedAt": t.get("completedAt")} for t in velocity_done[-5:]],
        },
        "agents": {
            "active": len(active_agents), "activeList": active_agents,
            "topContributors": [{"name": k, "tasks": v} for k, v in top_contributors[:5]],
        },
        "activity": {"last7d": len(recent_activity), "total": storage_activity_count()},
        "projects": {"distribution": [{"project": k, "tasks": v} for k, v in project_counts.items()]},
    })


# ---------------------------------------------------------------------------
# Daily Briefing
# ---------------------------------------------------------------------------

@app.route("/api/briefing", methods=["GET"])
def get_briefing():
    now = datetime.datetime.utcnow()
    today = now.strftime("%A, %B %d %Y")
    hour = now.hour
    period = "Morning" if hour < 12 else "Afternoon" if hour < 17 else "Evening"

    seven_days_ago = now - datetime.timedelta(days=7)
    yesterday = now - datetime.timedelta(days=1)

    tasks   = storage_get_tasks().get("tasks", [])
    done    = [t for t in tasks if t.get("status") == "done"]
    active  = [t for t in tasks if t.get("status") == "in-progress"]
    blocked = [t for t in tasks if t.get("is_blocked") or t.get("status") == "blocked"]
    ready   = [t for t in tasks if t.get("status") == "ready"]

    recently_done = []
    for t in done:
        ca = t.get("completedAt")
        if ca:
            try:
                if datetime.datetime.strptime(ca, "%Y-%m-%dT%H:%M:%SZ") >= yesterday:
                    recently_done.append(t)
            except ValueError:
                pass

    velocity_done = []
    for t in done:
        ca = t.get("completedAt")
        if ca:
            try:
                if datetime.datetime.strptime(ca, "%Y-%m-%dT%H:%M:%SZ") >= seven_days_ago:
                    velocity_done.append(t)
            except ValueError:
                pass

    projects_data   = storage_get_projects()
    active_projects = [p for p in projects_data.get("projects", []) if p.get("status") == "active"]
    at_risk         = [p for p in active_projects if p.get("health") in ("red", "yellow")]

    agents_data   = storage_get_agents()
    agent_list    = list(agents_data.get("agents", {}).values())
    active_agents = [a for a in agent_list if a.get("status") in ("active", "busy")]

    credits      = storage_get_credits()
    credit_warnings = []
    for provider, cdata in credits.get("providers", {}).items():
        balance = cdata.get("balance")
        budget  = credits.get("budget", {}).get("alertThresholds", {}).get(provider, {})
        warn_at = budget.get("warn", 20)
        if balance is not None and balance <= warn_at:
            credit_warnings.append({"provider": provider, "balance": balance})

    activity        = storage_get_activity(limit=200)
    recent_activity = [e for e in activity if _is_recent(e.get("timestamp", ""), yesterday)]

    highlights = []
    if recently_done:
        highlights.append(f"✅ {len(recently_done)} task(s) completed in the last 24h")
    if active_agents:
        names = ", ".join(a.get("name", "") for a in active_agents)
        highlights.append(f"🤖 Active agents: {names}")
    if blocked:
        highlights.append(f"🚫 {len(blocked)} task(s) blocked — need attention")
    if len(ready) > 0:
        highlights.append(f"📋 {len(ready)} task(s) ready and waiting to be picked up")
    if at_risk:
        highlights.append(f"⚠️ {len(at_risk)} project(s) at risk: {', '.join(p.get('name','') for p in at_risk[:3])}")
    if credit_warnings:
        highlights.append(f"💰 Credit warning: {', '.join(c['provider'] for c in credit_warnings)}")

    return jsonify({
        "generatedAt": now_iso(), "period": period, "date": today,
        "headline": f"{period} briefing — {today}",
        "highlights": highlights,
        "taskSummary": {
            "total": len(tasks), "done": len(done), "active": len(active),
            "blocked": len(blocked), "ready": len(ready), "velocity7d": len(velocity_done),
            "recentlyCompleted": [{"id": t["id"], "title": t["title"]} for t in recently_done],
        },
        "agentSummary": {
            "total": len(agent_list), "active": len(active_agents),
            "agents": [{"name": a.get("name"), "status": a.get("status"), "currentTask": a.get("currentTask")} for a in agent_list],
        },
        "projectSummary": {
            "active": len(active_projects), "atRisk": len(at_risk),
            "projects": [{"name": p.get("name"), "health": p.get("health"), "status": p.get("status")} for p in active_projects],
        },
        "activity24h": len(recent_activity),
        "alerts": [{"level": "warning", "message": f"Task blocked: {t['title']}"} for t in blocked],
    })


def _is_recent(ts_str: str, since: datetime.datetime) -> bool:
    try:
        return datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ") >= since
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Legacy search compat + GitHub
# ---------------------------------------------------------------------------

@app.route("/api/search/markdown", methods=["GET"])
def search_markdown():
    return unified_search()


@app.route("/api/search/semantic", methods=["GET"])
def search_semantic():
    return unified_search()


@app.route("/api/github/health", methods=["GET"])
def get_github_health():
    projects = storage_get_projects()
    repos = [p for p in projects.get("projects", []) if p.get("github_health_score") is not None]
    return jsonify({"repos": repos, "summary": {"total": len(repos)}})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
