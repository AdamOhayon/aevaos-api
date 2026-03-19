"""
AevaOS Mission Control API v1.2.0
Serves agent status, activity feeds, meeting rooms, tasks, projects,
credits, ideas, alerts, analytics and search data for the Mission Control.

v1.1.0 — Added write endpoints (PATCH tasks, POST ideas, POST activity, PATCH agents)
v1.2.0 — Added analytics summary, smart alerts, task creation, query filtering
"""

from flask import Flask, jsonify, request, abort
from flask_cors import CORS
import json
import os
import datetime

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

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
    """Helper to log activity to the feed."""
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
    return jsonify({
        "status": "ok",
        "service": "aevaos-api",
        "version": "1.2.0",
    })


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

    allowed = {"status", "currentTask", "lastActivity", "currentModel"}
    for key, val in data.items():
        if key in allowed:
            agents[agent_id][key] = val

    agents[agent_id]["last_updated"] = now_iso()
    registry["agents"] = agents
    write_json("agents-registry.json", registry)
    return jsonify(agents[agent_id])


@app.route("/api/office/activity", methods=["GET"])
def get_activity():
    limit = request.args.get("limit", 50, type=int)
    agent_filter = request.args.get("agent")
    entries = read_jsonl("activity-feed.jsonl", limit)
    if agent_filter:
        entries = [e for e in entries if e.get("agent") == agent_filter]
    return jsonify(entries)


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
    if "timestamp" in data:
        entry["timestamp"] = data["timestamp"]

    return jsonify(entry), 201


@app.route("/api/office/meeting-room", methods=["GET"])
def get_meeting_room():
    room = read_json("meeting-room.json")
    if not room:
        return jsonify({"rooms": {}, "metadata": {"totalRooms": 0}})
    return jsonify(room)


@app.route("/api/office/meeting-room/<room_id>", methods=["GET"])
def get_meeting_transcript(room_id):
    limit = request.args.get("limit", 50, type=int)
    transcript_file = os.path.join("transcripts", f"{room_id}.jsonl")
    entries = read_jsonl(transcript_file, limit)
    if not entries:
        abort(404, description="Meeting room not found")
    return jsonify(entries)


@app.route("/api/office/message", methods=["POST"])
def post_message():
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")

    room_id = data.get("room_id", "main-office")
    transcript_path = _data_path(os.path.join("transcripts", f"{room_id}.jsonl"))
    os.makedirs(os.path.dirname(transcript_path), exist_ok=True)

    with open(transcript_path, "a") as f:
        f.write(json.dumps(data) + "\n")

    return jsonify(data), 201


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
    tasks_data = read_json("tasks.json")
    if not tasks_data:
        return jsonify({"tasks": [], "version": 1})

    tasks = tasks_data.get("tasks", [])

    # Query filters
    status_filter = request.args.get("status")
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
    """Create a new task."""
    data = request.get_json()
    if not data or not data.get("title"):
        abort(400, description="'title' is required")

    tasks_data = read_json("tasks.json") or {"tasks": [], "version": 1}
    tasks = tasks_data.get("tasks", [])

    # Derive next ID
    existing_ids = [t["id"] for t in tasks if t["id"].startswith("TASK-")]
    max_num = 0
    for tid in existing_ids:
        try:
            max_num = max(max_num, int(tid.replace("TASK-", "")))
        except ValueError:
            pass
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
        "createdBy": data.get("createdBy", "mission-control-ui"),
        "last_updated": now_iso(),
        "activity_log": [{"timestamp": now_iso(), "status": "ready", "note": "Task created"}],
        "notes": [],
    }

    tasks.append(task)
    tasks_data["tasks"] = tasks
    write_json("tasks.json", tasks_data)

    append_activity(
        agent=data.get("createdBy", "ui"),
        action="task_created",
        message=f"New task created: {task['title']}",
        metadata={"taskId": next_id, "status": "ready"},
    )

    return jsonify(task), 201


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
    allowed = {"status", "assignee", "priority", "urgency", "is_blocked", "title", "description"}
    for key, val in data.items():
        if key in allowed:
            task[key] = val

    task["last_updated"] = now_iso()

    if "status" in data:
        task.setdefault("activity_log", []).append({
            "timestamp": now_iso(),
            "status": data["status"],
            "note": data.get("note", f"Status changed from {old_status} → {data['status']}")
        })
        if data["status"] == "done" and not task.get("completedAt"):
            task["completedAt"] = now_iso()
        if data["status"] == "in-progress" and not task.get("startedAt"):
            task["startedAt"] = now_iso()

        append_activity(
            agent=data.get("updatedBy", "ui"),
            action=f"task_{data['status'].replace('-', '_')}",
            message=f"{task['title']} → {data['status']}",
            metadata={"taskId": task_id},
        )

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
    projects = read_json("projects.json")
    if not projects:
        return jsonify({"projects": []})
    return jsonify(projects)


# ---------------------------------------------------------------------------
# Ideas
# ---------------------------------------------------------------------------

@app.route("/api/ideas", methods=["GET"])
def get_ideas():
    ideas = read_json("ideas.json")
    if not ideas:
        return jsonify({"ideas": []})
    return jsonify(ideas)


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
        "title": data.get("title", "Untitled Idea"),
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

    append_activity(
        agent=data.get("source", "api"),
        action="idea_captured",
        message=f"New idea: {idea['title']}",
        metadata={"ideaId": idea["id"]},
    )

    return jsonify(idea), 201


# ---------------------------------------------------------------------------
# Blockers
# ---------------------------------------------------------------------------

@app.route("/api/blockers", methods=["GET"])
def get_blockers():
    blockers = read_json("blockers.json")
    if not blockers:
        return jsonify({"history": []})
    return jsonify(blockers)


# ---------------------------------------------------------------------------
# Smart Alerts  (NEW in v1.2.0)
# ---------------------------------------------------------------------------

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    """
    Detect and return smart alerts:
    - Blocked tasks
    - Stale in-progress tasks (>48h no update)
    - Credit warnings
    - Agents that are offline unexpectedly
    """
    alerts = []
    now = datetime.datetime.utcnow()

    # 1. Blocked & stale tasks
    tasks_data = read_json("tasks.json") or {"tasks": []}
    for task in tasks_data.get("tasks", []):
        if task.get("is_blocked"):
            alerts.append({
                "id": f"blocked-{task['id']}",
                "level": "warning",
                "type": "task_blocked",
                "title": f"Task blocked: {task['title']}",
                "message": f"{task['id']} is marked as blocked",
                "taskId": task["id"],
                "timestamp": task.get("last_updated", now_iso()),
            })

        # Stale in-progress: no update in >48h
        if task.get("status") == "in-progress":
            last = task.get("last_updated") or task.get("startedAt")
            if last:
                try:
                    last_dt = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                    hours_stale = (now - last_dt).total_seconds() / 3600
                    if hours_stale > 48:
                        alerts.append({
                            "id": f"stale-{task['id']}",
                            "level": "warning",
                            "type": "task_stale",
                            "title": f"Stale task: {task['title']}",
                            "message": f"{task['id']} in-progress for {int(hours_stale)}h without update",
                            "taskId": task["id"],
                            "hoursStale": int(hours_stale),
                            "timestamp": now_iso(),
                        })
                except ValueError:
                    pass

    # 2. Credit warnings
    credits = read_json("credit-status.json") or {}
    for provider, data in credits.get("providers", {}).items():
        usage = data.get("usage", 0)
        limit = data.get("limit")
        threshold = data.get("alert_threshold", 80)
        if limit and limit > 0:
            pct = (usage / limit) * 100
            if pct >= threshold:
                level = "critical" if pct >= 95 else "warning"
                alerts.append({
                    "id": f"credit-{provider}",
                    "level": level,
                    "type": "credit_low",
                    "title": f"{'Critical' if level == 'critical' else 'Low'} credits: {provider}",
                    "message": f"{provider} at {pct:.0f}% of limit (${usage:.2f}/${limit:.2f})",
                    "provider": provider,
                    "timestamp": now_iso(),
                })

    # Sort: critical first, then warning
    alerts.sort(key=lambda a: 0 if a["level"] == "critical" else 1)

    return jsonify({
        "alerts": alerts,
        "count": len(alerts),
        "critical": len([a for a in alerts if a["level"] == "critical"]),
        "warnings": len([a for a in alerts if a["level"] == "warning"]),
        "generatedAt": now_iso(),
    })


# ---------------------------------------------------------------------------
# Analytics Summary  (NEW in v1.2.0)
# ---------------------------------------------------------------------------

@app.route("/api/analytics/summary", methods=["GET"])
def get_analytics_summary():
    """
    Computed metrics summary:
    - Task velocity (completed last 7d)
    - Completion rate (%)
    - Blocked count
    - Active agents
    - Top contributors
    - Recent completions
    """
    now = datetime.datetime.utcnow()
    seven_days_ago = now - datetime.timedelta(days=7)

    tasks_data = read_json("tasks.json") or {"tasks": []}
    tasks = tasks_data.get("tasks", [])

    total = len(tasks)
    done = [t for t in tasks if t.get("status") == "done"]
    in_progress = [t for t in tasks if t.get("status") == "in-progress"]
    blocked = [t for t in tasks if t.get("is_blocked") or t.get("status") == "blocked"]
    ready = [t for t in tasks if t.get("status") == "ready"]

    # Velocity: tasks completed in last 7 days
    recent_done = []
    for t in done:
        completed_at = t.get("completedAt")
        if completed_at:
            try:
                dt = datetime.datetime.strptime(completed_at, "%Y-%m-%dT%H:%M:%SZ")
                if dt >= seven_days_ago:
                    recent_done.append(t)
            except ValueError:
                pass

    # Completion rate
    completion_rate = round((len(done) / total * 100) if total > 0 else 0, 1)

    # Top assignees by task count
    assignee_counts = {}
    for t in tasks:
        a = t.get("assignee")
        if a:
            assignee_counts[a] = assignee_counts.get(a, 0) + 1
    top_contributors = sorted(assignee_counts.items(), key=lambda x: x[1], reverse=True)

    # Activity in last 7 days
    activity = read_jsonl("activity-feed.jsonl", 200)
    recent_activity = []
    for e in activity:
        try:
            dt = datetime.datetime.strptime(e.get("timestamp", ""), "%Y-%m-%dT%H:%M:%SZ")
            if dt >= seven_days_ago:
                recent_activity.append(e)
        except ValueError:
            pass

    # Active agents
    agents_data = read_json("agents-registry.json") or {"agents": {}}
    active_agents = [
        k for k, v in agents_data.get("agents", {}).items()
        if v.get("status") in ("active", "busy")
    ]

    # Project distribution
    project_counts = {}
    for t in tasks:
        p = t.get("project", "unassigned")
        project_counts[p] = project_counts.get(p, 0) + 1

    return jsonify({
        "generatedAt": now_iso(),
        "tasks": {
            "total": total,
            "done": len(done),
            "inProgress": len(in_progress),
            "blocked": len(blocked),
            "ready": len(ready),
            "completionRate": completion_rate,
            "velocity7d": len(recent_done),
            "recentCompletions": [{"id": t["id"], "title": t["title"], "completedAt": t.get("completedAt")} for t in recent_done[-5:]],
        },
        "agents": {
            "active": len(active_agents),
            "activeList": active_agents,
            "topContributors": [{"name": k, "tasks": v} for k, v in top_contributors[:5]],
        },
        "activity": {
            "last7d": len(recent_activity),
            "total": len(activity),
        },
        "projects": {
            "distribution": [{"project": k, "tasks": v} for k, v in project_counts.items()],
        },
    })


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.route("/api/search/markdown", methods=["GET"])
def search_markdown():
    query = request.args.get("q")
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400
    return jsonify({"results": [], "query": query, "message": "Search not yet connected"})


@app.route("/api/search/semantic", methods=["GET"])
def search_semantic():
    query = request.args.get("q")
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400
    return jsonify({"results": [], "query": query, "message": "Semantic search not yet connected"})


# ---------------------------------------------------------------------------
# GitHub integration
# ---------------------------------------------------------------------------

@app.route("/api/github/health", methods=["GET"])
def get_github_health():
    projects = read_json("projects.json")
    if not projects:
        return jsonify({"repos": [], "summary": {"total": 0}})
    repos = [
        p for p in projects.get("projects", [])
        if p.get("github_health_score") is not None
    ]
    return jsonify({
        "repos": repos,
        "summary": {
            "total": len(repos),
            "healthy": len([r for r in repos if r.get("github_health_status") == "healthy"]),
            "needs_attention": len([r for r in repos if r.get("github_health_status") == "needs_attention"]),
            "critical": len([r for r in repos if r.get("github_health_status") == "critical"]),
        },
    })


# ---------------------------------------------------------------------------
# Entry point (local dev)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
