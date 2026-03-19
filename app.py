"""
AevaOS Mission Control API
Serves agent status, activity feeds, meeting rooms, tasks, projects,
credits, ideas, and search data for the Mission Control dashboard.

Designed for deployment on Railway (auto-detects PORT env var).
Data is stored in bundled JSON files under the ./data directory.
"""

from flask import Flask, jsonify, request, abort
from flask_cors import CORS
import json
import os
import datetime

app = Flask(__name__)
CORS(app)  # Allow all origins (Vercel frontend, local dev, etc.)

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


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "aevaos-api",
        "version": "1.1.0",
    })


# ---------------------------------------------------------------------------
# Office routes  (matched by the frontend as /api/office/...)
# ---------------------------------------------------------------------------

@app.route("/api/office/agents", methods=["GET"])
def get_agents():
    agents = read_json("agents-registry.json")
    if not agents:
        return jsonify({"agents": {}, "metadata": {"totalAgents": 0}})
    return jsonify(agents)


@app.route("/api/office/agents/<agent_id>", methods=["PATCH"])
def update_agent(agent_id):
    """Update an agent's status, currentTask, lastActivity, etc."""
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
    entries = read_jsonl("activity-feed.jsonl", limit)
    return jsonify(entries)


@app.route("/api/office/activity", methods=["POST"])
def post_activity():
    """Log an activity event. Appends to activity-feed.jsonl."""
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")

    entry = {
        "timestamp": data.get("timestamp", now_iso()),
        "agent": data.get("agent", "unknown"),
        "action": data.get("action", "note"),
        "message": data.get("message", ""),
        "metadata": data.get("metadata", {}),
    }

    path = _data_path("activity-feed.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")

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
# Dashboard data routes
# ---------------------------------------------------------------------------

@app.route("/api/credits", methods=["GET"])
def get_credits():
    credits = read_json("credit-status.json")
    if not credits:
        return jsonify({"providers": {}, "lastChecked": None})
    return jsonify(credits)


@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    tasks = read_json("tasks.json")
    if not tasks:
        return jsonify({"tasks": [], "version": 1})
    return jsonify(tasks)


@app.route("/api/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id):
    """Update a task's status and optionally other fields."""
    data = request.get_json()
    if not data:
        abort(400, description="Invalid JSON payload")

    tasks_data = read_json("tasks.json") or {"tasks": []}
    tasks = tasks_data.get("tasks", [])
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        abort(404, description=f"Task '{task_id}' not found")

    allowed = {"status", "assignee", "priority", "urgency", "is_blocked"}
    for key, val in data.items():
        if key in allowed:
            task[key] = val

    task["last_updated"] = now_iso()

    # Append to activity_log
    if "status" in data:
        task.setdefault("activity_log", []).append({
            "timestamp": now_iso(),
            "status": data["status"],
            "note": data.get("note", f"Status updated to {data['status']}")
        })
        # Set completion timestamp
        if data["status"] == "done" and not task.get("completedAt"):
            task["completedAt"] = now_iso()

    write_json("tasks.json", tasks_data)
    return jsonify(task)


@app.route("/api/projects", methods=["GET"])
def get_projects():
    projects = read_json("projects.json")
    if not projects:
        return jsonify({"projects": []})
    return jsonify(projects)


@app.route("/api/ideas", methods=["GET"])
def get_ideas():
    ideas = read_json("ideas.json")
    if not ideas:
        return jsonify({"ideas": []})
    return jsonify(ideas)


@app.route("/api/ideas", methods=["POST"])
def post_idea():
    """Capture a new idea from any source (UI, Telegram, agent)."""
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

    # Also log to activity feed
    activity_path = _data_path("activity-feed.jsonl")
    with open(activity_path, "a") as f:
        f.write(json.dumps({
            "timestamp": idea["capturedAt"],
            "agent": data.get("source", "api"),
            "action": "idea_captured",
            "message": f"New idea captured: {idea['title']}",
            "metadata": {"ideaId": idea["id"]},
        }) + "\n")

    return jsonify(idea), 201


@app.route("/api/blockers", methods=["GET"])
def get_blockers():
    blockers = read_json("blockers.json")
    if not blockers:
        return jsonify({"history": []})
    return jsonify(blockers)


# ---------------------------------------------------------------------------
# Search (placeholder — returns empty results until qmd is connected)
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
# GitHub integration routes
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
