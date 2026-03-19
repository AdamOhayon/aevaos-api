"""
AevaOS Mission Control API v1.3.0
v1.1.0 — Write endpoints (PATCH tasks, POST ideas, POST activity, PATCH agents)
v1.2.0 — Smart alerts, analytics summary, POST tasks, task filtering
v1.3.0 — Daily briefing, SSE activity stream, task detail, unified search, blockers write
"""

from flask import Flask, jsonify, request, abort, Response, stream_with_context
from flask_cors import CORS
import json
import os
import datetime
import time

app = Flask(__name__)
CORS(app)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _data_path(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)


def read_json(filename: str):
    path = _data_path(filename)
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def write_json(filename: str, data):
    path = _data_path(filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def read_jsonl(filename: str, limit: int = 50) -> list:
    path = _data_path(filename)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]


def now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def append_activity(agent: str, action: str, message: str, metadata: dict = None):
    entry = {
        "timestamp": now_iso(),
        "agent": agent,
        "action": action,
        "message": message,
        "metadata": metadata or {},
    }
    path = _data_path("activity-feed.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "aevaos-api", "version": "1.3.0"})


# ---------------------------------------------------------------------------
# Office routes
# ---------------------------------------------------------------------------

@app.route("/api/office/agents", methods=["GET"])
def get_agents():
    agents = read_json("agents-registry.json")
    if not agents:
        return jsonify({"agents": {}, "metadata": {"totalAgents": 0}})
    return jsonify(agents)


@app.route("/api/office/agents/<agent_id>", methods=["PATCH"])
def update_agent(agent_id):
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    registry = read_json("agents-registry.json") or {"agents": {}, "metadata": {}}
    agents = registry.get("agents", {})
    if agent_id not in agents:
        abort(404, description=f"Agent '{agent_id}' not found")
    for key, val in data.items():
        if key in {"status", "currentTask", "lastActivity", "currentModel"}:
            agents[agent_id][key] = val
    agents[agent_id]["last_updated"] = now_iso()
    registry["agents"] = agents
    write_json("agents-registry.json", registry)
    return jsonify(agents[agent_id])


@app.route("/api/office/activity", methods=["GET"])
def get_activity():
    limit = request.args.get("limit", 50, type=int)
    agent_filter = request.args.get("agent")
    since = request.args.get("since")  # ISO timestamp for polling new-only
    entries = read_jsonl("activity-feed.jsonl", 500)  # read more for filtering
    if agent_filter:
        entries = [e for e in entries if e.get("agent") == agent_filter]
    if since:
        try:
            since_dt = datetime.datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ")
            entries = [e for e in entries if datetime.datetime.strptime(e.get("timestamp", "2000-01-01T00:00:00Z"), "%Y-%m-%dT%H:%M:%SZ") > since_dt]
        except ValueError:
            pass
    return jsonify(entries[-limit:])


@app.route("/api/office/activity", methods=["POST"])
def post_activity():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    entry = append_activity(
        agent=data.get("agent", "unknown"),
        action=data.get("action", "note"),
        message=data.get("message", ""),
        metadata=data.get("metadata", {}),
    )
    return jsonify(entry), 201


# ---------------------------------------------------------------------------
# SSE Activity Stream  (NEW v1.3.0)
# ---------------------------------------------------------------------------

@app.route("/api/stream/activity", methods=["GET"])
def stream_activity():
    """
    Server-Sent Events endpoint — streams new activity events as they arrive.
    Polls the JSONL file every 2 seconds and pushes new entries.
    Client reconnects automatically via EventSource.
    """
    def generate():
        last_count = len(read_jsonl("activity-feed.jsonl", 1000))
        # Send initial heartbeat
        yield "event: heartbeat\ndata: {\"ts\": \"" + now_iso() + "\"}\n\n"
        while True:
            time.sleep(2)
            entries = read_jsonl("activity-feed.jsonl", 1000)
            if len(entries) > last_count:
                new_entries = entries[last_count:]
                for entry in new_entries:
                    yield f"data: {json.dumps(entry)}\n\n"
                last_count = len(entries)
            else:
                # heartbeat to keep connection alive
                yield "event: heartbeat\ndata: {\"ts\": \"" + now_iso() + "\"}\n\n"

    resp = Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
    )
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
    entries = read_jsonl(os.path.join("transcripts", f"{room_id}.jsonl"), limit)
    if not entries:
        return jsonify([])
    return jsonify(entries)


@app.route("/api/office/message", methods=["POST"])
def post_message():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    room_id = data.get("room_id", "main-office")
    transcript_path = _data_path(os.path.join("transcripts", f"{room_id}.jsonl"))
    os.makedirs(os.path.dirname(transcript_path), exist_ok=True)
    msg = {
        "timestamp": data.get("timestamp", now_iso()),
        "from": data.get("from", "user"),
        "to": data.get("to", "all"),
        "message": data.get("message", ""),
        "type": data.get("type", "message"),
    }
    with open(transcript_path, "a") as f:
        f.write(json.dumps(msg) + "\n")
    append_activity(
        agent=msg["from"],
        action="message",
        message=f"[{room_id}] {msg['message'][:80]}",
        metadata={"room": room_id},
    )
    return jsonify(msg), 201


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

@app.route("/api/credits", methods=["GET"])
def get_credits():
    credits = read_json("credit-status.json")
    if not credits:
        return jsonify({"providers": {}, "lastChecked": None})
    return jsonify(credits)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    tasks_data = read_json("tasks.json") or {"tasks": [], "version": 1}
    tasks = tasks_data.get("tasks", [])
    status_filter  = request.args.get("status")
    assignee_filter = request.args.get("assignee")
    priority_filter = request.args.get("priority")
    if status_filter:
        tasks = [t for t in tasks if t.get("status") == status_filter]
    if assignee_filter:
        tasks = [t for t in tasks if t.get("assignee") == assignee_filter]
    if priority_filter:
        tasks = [t for t in tasks if t.get("urgency") == priority_filter or t.get("priority") == priority_filter]
    tasks_data["tasks"] = tasks
    return jsonify(tasks_data)


@app.route("/api/tasks", methods=["POST"])
def create_task():
    data = request.get_json()
    if not data or not data.get("title"):
        abort(400, description="'title' is required")
    tasks_data = read_json("tasks.json") or {"tasks": [], "version": 1}
    tasks = tasks_data.get("tasks", [])
    existing_ids = [t["id"] for t in tasks if t["id"].startswith("TASK-")]
    max_num = max((int(tid.replace("TASK-", "")) for tid in existing_ids if tid.replace("TASK-", "").isdigit()), default=0)
    next_id = f"TASK-{max_num + 1:03d}"
    task = {
        "id": next_id,
        "title": data["title"],
        "description": data.get("description", ""),
        "project": data.get("project", "aeva-os"),
        "status": data.get("status", "ready"),
        "assignee": data.get("assignee", ""),
        "urgency": data.get("urgency", "medium"),
        "complexity": data.get("complexity", "medium"),
        "priority": data.get("priority", "medium"),
        "is_blocked": False,
        "days_stuck": 0,
        "createdAt": now_iso(),
        "createdBy": data.get("createdBy", "ui"),
        "last_updated": now_iso(),
        "activity_log": [{"timestamp": now_iso(), "status": "ready", "note": "Task created"}],
        "notes": [],
    }
    tasks.append(task)
    tasks_data["tasks"] = tasks
    write_json("tasks.json", tasks_data)
    append_activity(agent=data.get("createdBy", "ui"), action="task_created",
                    message=f"New task: {task['title']}", metadata={"taskId": next_id})
    return jsonify(task), 201


@app.route("/api/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    """Full task detail with complete activity log."""
    tasks_data = read_json("tasks.json") or {"tasks": []}
    task = next((t for t in tasks_data.get("tasks", []) if t["id"] == task_id), None)
    if not task:
        abort(404, description=f"Task '{task_id}' not found")
    return jsonify(task)


@app.route("/api/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id):
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    tasks_data = read_json("tasks.json") or {"tasks": []}
    tasks = tasks_data.get("tasks", [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        abort(404, description=f"Task '{task_id}' not found")
    old_status = task.get("status")
    for key, val in data.items():
        if key in {"status", "assignee", "priority", "urgency", "is_blocked", "title", "description"}:
            task[key] = val
    task["last_updated"] = now_iso()
    if "status" in data:
        task.setdefault("activity_log", []).append({
            "timestamp": now_iso(), "status": data["status"],
            "note": data.get("note", f"Status changed {old_status} → {data['status']}")
        })
        if data["status"] == "done" and not task.get("completedAt"):
            task["completedAt"] = now_iso()
        if data["status"] == "in-progress" and not task.get("startedAt"):
            task["startedAt"] = now_iso()
        append_activity(agent=data.get("updatedBy", "ui"),
                        action=f"task_{data['status'].replace('-', '_')}",
                        message=f"{task['title']} → {data['status']}",
                        metadata={"taskId": task_id})
    write_json("tasks.json", tasks_data)
    return jsonify(task)


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    tasks_data = read_json("tasks.json") or {"tasks": []}
    tasks = tasks_data.get("tasks", [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        abort(404, description=f"Task '{task_id}' not found")
    tasks_data["tasks"] = [t for t in tasks if t["id"] != task_id]
    write_json("tasks.json", tasks_data)
    return jsonify({"deleted": task_id})


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.route("/api/projects", methods=["GET"])
def get_projects():
    return jsonify(read_json("projects.json") or {"projects": []})


# ---------------------------------------------------------------------------
# Ideas
# ---------------------------------------------------------------------------

@app.route("/api/ideas", methods=["GET"])
def get_ideas():
    return jsonify(read_json("ideas.json") or {"ideas": []})


@app.route("/api/ideas", methods=["POST"])
def post_idea():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")
    ideas_data = read_json("ideas.json") or {"ideas": [], "nextId": 1}
    ideas = ideas_data.get("ideas", [])
    next_id = ideas_data.get("nextId", len(ideas) + 1)
    idea = {
        "id": f"IDEA-{next_id:03d}",
        "title": data.get("title", "Untitled"),
        "description": data.get("description", ""),
        "category": data.get("category", "research"),
        "source": data.get("source", "api"),
        "capturedAt": now_iso(),
        "status": "new",
        "tags": data.get("tags", []),
    }
    ideas.append(idea)
    ideas_data["ideas"] = ideas
    ideas_data["nextId"] = next_id + 1
    write_json("ideas.json", ideas_data)
    append_activity(agent=data.get("source", "api"), action="idea_captured",
                    message=f"New idea: {idea['title']}", metadata={"ideaId": idea["id"]})
    return jsonify(idea), 201


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------

@app.route("/api/blockers", methods=["GET"])
def get_blockers():
    return jsonify(read_json("blockers.json") or {"history": []})


@app.route("/api/blockers", methods=["POST"])
def post_blocker():
    """Record a new blocker detection snapshot."""
    data = request.get_json() or {}
    blockers_data = read_json("blockers.json") or {"history": []}
    entry = {
        "detected_at": data.get("detected_at", now_iso()),
        "blockers": data.get("blockers", []),
        "source": data.get("source", "manual"),
    }
    blockers_data["history"].append(entry)
    write_json("blockers.json", blockers_data)
    return jsonify(entry), 201


# ---------------------------------------------------------------------------
# Unified Search  (NEW v1.3.0)
# ---------------------------------------------------------------------------

@app.route("/api/search", methods=["GET"])
def unified_search():
    """
    Search across tasks, ideas, and projects by keyword.
    Returns ranked results with type and match context.
    """
    query = (request.args.get("q") or "").lower().strip()
    if not query or len(query) < 2:
        return jsonify({"results": [], "query": query, "count": 0})

    results = []

    # Search tasks
    tasks_data = read_json("tasks.json") or {"tasks": []}
    for t in tasks_data.get("tasks", []):
        score = 0
        text = f"{t.get('title','')} {t.get('description','')} {t.get('project','')} {t.get('assignee','')}".lower()
        if query in text:
            if query in t.get("title", "").lower():
                score += 10
            score += text.count(query) * 2
            results.append({
                "type": "task",
                "id": t["id"],
                "title": t["title"],
                "description": t.get("description", "")[:120],
                "meta": {"status": t.get("status"), "assignee": t.get("assignee"), "project": t.get("project")},
                "score": score,
                "href": "/tasks",
            })

    # Search ideas
    ideas_data = read_json("ideas.json") or {"ideas": []}
    for idea in ideas_data.get("ideas", []):
        text = f"{idea.get('title','')} {idea.get('description','')} {' '.join(idea.get('tags', []))}".lower()
        if query in text:
            score = 10 if query in idea.get("title", "").lower() else 5
            results.append({
                "type": "idea",
                "id": idea["id"],
                "title": idea["title"],
                "description": idea.get("description", "")[:120],
                "meta": {"category": idea.get("category"), "status": idea.get("status")},
                "score": score,
                "href": "/ideas",
            })

    # Search projects
    projects_data = read_json("projects.json") or {"projects": []}
    for p in projects_data.get("projects", []):
        text = f"{p.get('name','')} {p.get('description','')} {p.get('id','')}".lower()
        if query in text:
            score = 15 if query in p.get("name", "").lower() else 7
            results.append({
                "type": "project",
                "id": p["id"],
                "title": p["name"],
                "description": p.get("description", "")[:120],
                "meta": {"status": p.get("status"), "health": p.get("health")},
                "score": score,
                "href": "/projects",
            })

    results.sort(key=lambda r: r["score"], reverse=True)

    return jsonify({
        "results": results[:20],
        "query": query,
        "count": len(results),
    })


# ---------------------------------------------------------------------------
# Daily Briefing  (NEW v1.3.0)
# ---------------------------------------------------------------------------

@app.route("/api/briefing", methods=["GET"])
def get_briefing():
    """
    Computed daily situational awareness briefing.
    Aggregates state of all systems into a human/agent-readable report.
    """
    now = datetime.datetime.utcnow()
    today = now.strftime("%A, %B %d %Y")
    hour = now.hour
    period = "Morning" if hour < 12 else "Afternoon" if hour < 17 else "Evening"

    seven_days_ago = now - datetime.timedelta(days=7)
    yesterday = now - datetime.timedelta(days=1)

    # Tasks
    tasks_data = read_json("tasks.json") or {"tasks": []}
    tasks = tasks_data.get("tasks", [])
    done    = [t for t in tasks if t.get("status") == "done"]
    active  = [t for t in tasks if t.get("status") == "in-progress"]
    blocked = [t for t in tasks if t.get("is_blocked") or t.get("status") == "blocked"]
    ready   = [t for t in tasks if t.get("status") == "ready"]

    # Recently completed (last 24h)
    recently_done = []
    for t in done:
        completed_at = t.get("completedAt")
        if completed_at:
            try:
                dt = datetime.datetime.strptime(completed_at, "%Y-%m-%dT%H:%M:%SZ")
                if dt >= yesterday:
                    recently_done.append(t)
            except ValueError:
                pass

    # Velocity
    velocity_done = []
    for t in done:
        completed_at = t.get("completedAt")
        if completed_at:
            try:
                dt = datetime.datetime.strptime(completed_at, "%Y-%m-%dT%H:%M:%SZ")
                if dt >= seven_days_ago:
                    velocity_done.append(t)
            except ValueError:
                pass

    # Projects
    projects_data = read_json("projects.json") or {"projects": []}
    active_projects = [p for p in projects_data.get("projects", []) if p.get("status") == "active"]
    at_risk = [p for p in active_projects if p.get("health") in ("red", "yellow")]

    # Agents
    agents_data = read_json("agents-registry.json") or {"agents": {}}
    agent_list = list(agents_data.get("agents", {}).values())
    active_agents = [a for a in agent_list if a.get("status") in ("active", "busy")]

    # Credits
    credits_data = read_json("credit-status.json") or {}
    credit_warnings = []
    for provider, cdata in credits_data.get("providers", {}).items():
        usage = cdata.get("usage", 0)
        limit = cdata.get("limit")
        threshold = cdata.get("alert_threshold", 80)
        if limit and limit > 0 and (usage / limit * 100) >= threshold:
            credit_warnings.append({"provider": provider, "pct": round(usage / limit * 100, 1)})

    # Activity last 24h
    activity = read_jsonl("activity-feed.jsonl", 200)
    recent_activity = [e for e in activity if _is_recent(e.get("timestamp", ""), yesterday)]

    # Alerts
    alerts_list = []
    for t in blocked:
        alerts_list.append({"level": "warning", "message": f"Task blocked: {t['title']}"})
    for cw in credit_warnings:
        alerts_list.append({"level": "warning", "message": f"{cw['provider']} credits at {cw['pct']}%"})

    # Highlights — what's worth mentioning
    highlights = []
    if recently_done:
        highlights.append(f"✅ {len(recently_done)} task(s) completed in the last 24h")
    if active_agents:
        names = ", ".join(a.get("name", k) for k, a in agents_data.get("agents", {}).items() if a.get("status") in ("active", "busy"))
        highlights.append(f"🤖 Active agents: {names}")
    if blocked:
        highlights.append(f"🚫 {len(blocked)} task(s) blocked — need attention")
    if len(ready) > 0:
        highlights.append(f"📋 {len(ready)} task(s) ready and waiting to be picked up")
    if at_risk:
        highlights.append(f"⚠️ {len(at_risk)} project(s) at risk: {', '.join(p['name'] for p in at_risk[:3])}")
    if credit_warnings:
        highlights.append(f"💰 Credit warning: {', '.join(c['provider'] for c in credit_warnings)}")

    return jsonify({
        "generatedAt": now_iso(),
        "period": period,
        "date": today,
        "headline": f"{period} briefing — {today}",
        "highlights": highlights,
        "taskSummary": {
            "total": len(tasks),
            "done": len(done),
            "active": len(active),
            "blocked": len(blocked),
            "ready": len(ready),
            "velocity7d": len(velocity_done),
            "recentlyCompleted": [{"id": t["id"], "title": t["title"]} for t in recently_done],
        },
        "agentSummary": {
            "total": len(agent_list),
            "active": len(active_agents),
            "agents": [{"name": a.get("name"), "status": a.get("status"), "currentTask": a.get("currentTask")} for a in agent_list],
        },
        "projectSummary": {
            "active": len(active_projects),
            "atRisk": len(at_risk),
            "projects": [{"name": p["name"], "health": p.get("health"), "status": p.get("status")} for p in active_projects],
        },
        "activity24h": len(recent_activity),
        "alerts": alerts_list,
    })


def _is_recent(ts_str: str, since: datetime.datetime) -> bool:
    try:
        dt = datetime.datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt >= since
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Smart Alerts
# ---------------------------------------------------------------------------

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    alerts = []
    now = datetime.datetime.utcnow()

    tasks_data = read_json("tasks.json") or {"tasks": []}
    for task in tasks_data.get("tasks", []):
        if task.get("is_blocked"):
            alerts.append({"id": f"blocked-{task['id']}", "level": "warning", "type": "task_blocked",
                           "title": f"Task blocked: {task['title']}", "message": f"{task['id']} is marked as blocked",
                           "taskId": task["id"], "timestamp": task.get("last_updated", now_iso())})
        if task.get("status") == "in-progress":
            last = task.get("last_updated") or task.get("startedAt")
            if last:
                try:
                    last_dt = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                    hours_stale = (now - last_dt).total_seconds() / 3600
                    if hours_stale > 48:
                        alerts.append({"id": f"stale-{task['id']}", "level": "warning", "type": "task_stale",
                                       "title": f"Stale task: {task['title']}",
                                       "message": f"{task['id']} in-progress for {int(hours_stale)}h without update",
                                       "taskId": task["id"], "hoursStale": int(hours_stale), "timestamp": now_iso()})
                except ValueError:
                    pass

    credits = read_json("credit-status.json") or {}
    for provider, data in credits.get("providers", {}).items():
        status = data.get("status", "unknown")
        if status in ("unlimited", "unknown"):
            continue

        balance   = data.get("balance")   # remaining $ (OpenRouter style)
        usage     = data.get("usage")     # $ spent
        limit     = data.get("limit")     # monthly $ cap
        threshold = data.get("alert_threshold")  # % based override

        # --- Balance-remaining style (OpenRouter: balance < warn threshold) ---
        budget_thresholds = credits.get("budget", {}).get("alertThresholds", {}).get(provider, {})
        warn_at     = budget_thresholds.get("warn", 20)     # $ remaining
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
                    "id": f"credit-{provider}",
                    "level": level,
                    "type": "credit_low",
                    "title": f"{'Critical' if level == 'critical' else 'Low'} balance: {provider}",
                    "message": f"${balance:.2f} remaining (warn at ${warn_at})",
                    "provider": provider,
                    "timestamp": now_iso(),
                })
            continue

        # --- Percentage-based style (Anthropic tokens via usage/limit) ---
        if threshold is None:
            threshold = 80
        if limit and limit > 0 and usage is not None:
            pct = (usage / limit) * 100
            if pct >= threshold:
                level = "critical" if pct >= 95 else "warning"
                alerts.append({
                    "id": f"credit-{provider}",
                    "level": level,
                    "type": "credit_low",
                    "title": f"{'Critical' if level == 'critical' else 'Low'} credits: {provider}",
                    "message": f"{provider} at {pct:.0f}% of limit",
                    "provider": provider,
                    "timestamp": now_iso(),
                })

    alerts.sort(key=lambda a: 0 if a["level"] == "critical" else 1)
    return jsonify({"alerts": alerts, "count": len(alerts), "critical": len([a for a in alerts if a["level"] == "critical"]),
                    "warnings": len([a for a in alerts if a["level"] == "warning"]), "generatedAt": now_iso()})


# ---------------------------------------------------------------------------
# Analytics Summary
# ---------------------------------------------------------------------------

@app.route("/api/analytics/summary", methods=["GET"])
def get_analytics_summary():
    now = datetime.datetime.utcnow()
    seven_days_ago = now - datetime.timedelta(days=7)

    tasks_data = read_json("tasks.json") or {"tasks": []}
    tasks = tasks_data.get("tasks", [])
    done_tasks = [t for t in tasks if t.get("status") == "done"]
    in_progress = [t for t in tasks if t.get("status") == "in-progress"]
    blocked = [t for t in tasks if t.get("is_blocked") or t.get("status") == "blocked"]
    ready = [t for t in tasks if t.get("status") == "ready"]

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

    activity = read_jsonl("activity-feed.jsonl", 200)
    recent_activity = [e for e in activity if _is_recent(e.get("timestamp", ""), seven_days_ago)]

    agents_data = read_json("agents-registry.json") or {"agents": {}}
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
        "activity": {"last7d": len(recent_activity), "total": len(activity)},
        "projects": {"distribution": [{"project": k, "tasks": v} for k, v in project_counts.items()]},
    })


# ---------------------------------------------------------------------------
# Search (legacy endpoints kept for backward compat)
# ---------------------------------------------------------------------------

@app.route("/api/search/markdown", methods=["GET"])
def search_markdown():
    q = request.args.get("q", "")
    return unified_search() if q else jsonify({"results": [], "query": ""})


@app.route("/api/search/semantic", methods=["GET"])
def search_semantic():
    q = request.args.get("q", "")
    return unified_search() if q else jsonify({"results": [], "query": ""})


# ---------------------------------------------------------------------------
# GitHub integration
# ---------------------------------------------------------------------------

@app.route("/api/github/health", methods=["GET"])
def get_github_health():
    projects = read_json("projects.json")
    if not projects:
        return jsonify({"repos": [], "summary": {"total": 0}})
    repos = [p for p in projects.get("projects", []) if p.get("github_health_score") is not None]
    return jsonify({"repos": repos, "summary": {"total": len(repos)}})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
