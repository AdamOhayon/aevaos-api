"""
AevaOS Storage Abstraction Layer (v1.4.0)

Routes all reads/writes through SQLAlchemy when DATABASE_URL is set,
falls back to raw JSON file helpers when it is not.
Importing this module is safe regardless of which mode is active.
"""
import os
import json
import datetime

# ── DB availability flag ──────────────────────────────────────────────────────
_raw_url = os.environ.get("DATABASE_URL", "")
USE_DB = bool(_raw_url)

if USE_DB:
    from models import (
        db, Task, Idea, Project, Agent,
        ActivityEntry, MeetingMessage, BlockerScan,
    )

# ── JSON helpers (always available as fallback) ───────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _data_path(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)


def _read_json(filename: str):
    path = _data_path(filename)
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def _write_json(filename: str, data):
    path = _data_path(filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _read_jsonl(filename: str, limit: int = 50) -> list:
    path = _data_path(filename)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]


def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(s: str) -> datetime.datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ── Tasks ─────────────────────────────────────────────────────────────────────

def storage_get_tasks(status=None, assignee=None, priority=None, project=None) -> dict:
    if USE_DB:
        q = Task.query
        if status:
            q = q.filter(Task.status == status)
        if assignee:
            q = q.filter(Task.assignee == assignee)
        if priority:
            q = q.filter((Task.urgency == priority) | (Task.priority == priority))
        if project:
            q = q.filter(Task.project == project)
        tasks = [t.to_dict() for t in q.all()]
        return {"tasks": tasks, "version": 2}

    data = _read_json("tasks.json") or {"tasks": [], "version": 1}
    tasks = data.get("tasks", [])
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    if assignee:
        tasks = [t for t in tasks if t.get("assignee") == assignee]
    if priority:
        tasks = [t for t in tasks if t.get("urgency") == priority or t.get("priority") == priority]
    if project:
        tasks = [t for t in tasks if t.get("project") == project]
    data["tasks"] = tasks
    return data


def storage_get_task(task_id: str) -> dict | None:
    if USE_DB:
        t = Task.query.get(task_id)
        return t.to_dict() if t else None

    data = _read_json("tasks.json") or {"tasks": []}
    return next((t for t in data.get("tasks", []) if t["id"] == task_id), None)


def storage_save_task(task: dict) -> dict:
    """Insert or update a task. Accepts dict with camelCase or snake_case keys."""
    if USE_DB:
        existing = Task.query.get(task["id"])
        if existing:
            for k, v in task.items():
                col_map = {
                    "title": "title", "description": "description", "project": "project",
                    "status": "status", "assignee": "assignee", "urgency": "urgency",
                    "impact": "impact", "effort": "effort", "complexity": "complexity",
                    "priority": "priority", "is_blocked": "is_blocked",
                    "days_stuck": "days_stuck", "priority_score": "priority_score",
                    "activity_log": "activity_log", "notes": "notes",
                }
                if k in col_map:
                    setattr(existing, col_map[k], v)
            for iso_key, attr in [("completedAt", "completed_at"), ("startedAt", "started_at")]:
                if iso_key in task:
                    setattr(existing, attr, _parse_dt(task[iso_key]))
            existing.last_updated = datetime.datetime.utcnow()
            db.session.commit()
            return existing.to_dict()
        else:
            row = Task(
                id=task["id"],
                title=task.get("title", ""),
                description=task.get("description", ""),
                project=task.get("project", "aeva-os"),
                status=task.get("status", "ready"),
                assignee=task.get("assignee", ""),
                urgency=task.get("urgency", "medium"),
                impact=task.get("impact", "medium"),
                effort=task.get("effort", "medium"),
                complexity=task.get("complexity", "medium"),
                priority=task.get("priority", "medium"),
                priority_score=task.get("priority_score", 0),
                is_blocked=task.get("is_blocked", False),
                days_stuck=task.get("days_stuck", 0),
                created_at=_parse_dt(task.get("createdAt")) or datetime.datetime.utcnow(),
                created_by=task.get("createdBy", "ui"),
                completed_at=_parse_dt(task.get("completedAt")),
                started_at=_parse_dt(task.get("startedAt")),
                activity_log=task.get("activity_log", []),
                notes=task.get("notes", []),
            )
            db.session.add(row)
            db.session.commit()
            return row.to_dict()

    # JSON fallback
    data = _read_json("tasks.json") or {"tasks": [], "version": 1}
    tasks = data.get("tasks", [])
    idx = next((i for i, t in enumerate(tasks) if t["id"] == task["id"]), None)
    if idx is not None:
        tasks[idx] = task
    else:
        tasks.append(task)
    data["tasks"] = tasks
    _write_json("tasks.json", data)
    return task


def storage_delete_task(task_id: str) -> bool:
    if USE_DB:
        t = Task.query.get(task_id)
        if not t:
            return False
        db.session.delete(t)
        db.session.commit()
        return True

    data = _read_json("tasks.json") or {"tasks": []}
    original = len(data.get("tasks", []))
    data["tasks"] = [t for t in data.get("tasks", []) if t["id"] != task_id]
    _write_json("tasks.json", data)
    return len(data["tasks"]) < original


# ── Ideas ─────────────────────────────────────────────────────────────────────

def storage_get_ideas() -> dict:
    if USE_DB:
        ideas = [i.to_dict() for i in Idea.query.order_by(Idea.captured_at.desc()).all()]
        return {"ideas": ideas}

    return _read_json("ideas.json") or {"ideas": []}


def storage_save_idea(idea: dict) -> dict:
    if USE_DB:
        existing = Idea.query.get(idea["id"])
        if existing:
            for attr, key in [("title", "title"), ("description", "description"),
                               ("category", "category"), ("status", "status"), ("tags", "tags")]:
                if key in idea:
                    setattr(existing, attr, idea[key])
            db.session.commit()
            return existing.to_dict()
        row = Idea(
            id=idea["id"], title=idea.get("title", ""), description=idea.get("description", ""),
            category=idea.get("category", "research"), status=idea.get("status", "new"),
            source=idea.get("source", "api"),
            captured_at=_parse_dt(idea.get("capturedAt")) or datetime.datetime.utcnow(),
            tags=idea.get("tags", []), notion_url=idea.get("notionUrl"),
        )
        db.session.add(row)
        db.session.commit()
        return row.to_dict()

    data = _read_json("ideas.json") or {"ideas": [], "nextId": 1}
    ideas = data.get("ideas", [])
    idx = next((i for i, item in enumerate(ideas) if item["id"] == idea["id"]), None)
    if idx is not None:
        ideas[idx] = idea
    else:
        ideas.append(idea)
    data["ideas"] = ideas
    _write_json("ideas.json", data)
    return idea


def storage_next_idea_id() -> str:
    if USE_DB:
        last = Idea.query.order_by(Idea.id.desc()).first()
        if last:
            num = int(last.id.replace("IDEA-", "")) + 1 if last.id.startswith("IDEA-") else 1
        else:
            num = 1
        return f"IDEA-{num:03d}"

    data = _read_json("ideas.json") or {"ideas": [], "nextId": 1}
    next_id = data.get("nextId", len(data.get("ideas", [])) + 1)
    data["nextId"] = next_id + 1
    _write_json("ideas.json", data)
    return f"IDEA-{next_id:03d}"


# ── Projects ──────────────────────────────────────────────────────────────────

def storage_get_projects() -> dict:
    if USE_DB:
        projects = [p.to_dict() for p in Project.query.order_by(Project.priority).all()]
        return {"projects": projects}

    return _read_json("projects.json") or {"projects": []}


# ── Agents ────────────────────────────────────────────────────────────────────

def storage_get_agents() -> dict:
    if USE_DB:
        agents = {a.id: a.to_dict() for a in Agent.query.all()}
        active = sum(1 for a in agents.values() if a["status"] in ("active", "busy"))
        return {
            "version": 2, "lastUpdated": _now_iso(),
            "agents": agents,
            "metadata": {"totalAgents": len(agents), "activeAgents": active, "idleAgents": len(agents) - active},
        }

    return _read_json("agents-registry.json") or {"agents": {}, "metadata": {"totalAgents": 0}}


def storage_save_agent(agent_id: str, updates: dict) -> dict:
    if USE_DB:
        a = Agent.query.get(agent_id)
        if not a:
            return {}
        field_map = {
            "status": "status", "currentTask": "current_task",
            "lastActivity": "last_activity", "currentModel": "current_model",
        }
        for key, attr in field_map.items():
            if key in updates:
                setattr(a, attr, updates[key])
        a.last_seen = datetime.datetime.utcnow()
        db.session.commit()
        return a.to_dict()

    registry = _read_json("agents-registry.json") or {"agents": {}, "metadata": {}}
    agents = registry.get("agents", {})
    if agent_id not in agents:
        return {}
    for key, val in updates.items():
        if key in {"status", "currentTask", "lastActivity", "currentModel"}:
            agents[agent_id][key] = val
    agents[agent_id]["last_updated"] = _now_iso()
    registry["agents"] = agents
    _write_json("agents-registry.json", registry)
    return agents[agent_id]


# ── Activity Feed ─────────────────────────────────────────────────────────────

def storage_append_activity(agent: str, action: str, message: str, metadata: dict = None) -> dict:
    entry_dict = {
        "timestamp": _now_iso(), "agent": agent,
        "action": action, "message": message, "metadata": metadata or {},
    }
    if USE_DB:
        row = ActivityEntry(
            timestamp=datetime.datetime.utcnow(),
            agent=agent, action=action, message=message, meta=metadata or {},
        )
        db.session.add(row)
        db.session.commit()
    else:
        path = _data_path("activity-feed.jsonl")
        with open(path, "a") as f:
            f.write(json.dumps(entry_dict) + "\n")
    return entry_dict


def storage_get_activity(limit: int = 50, agent_filter: str = None, since: str = None) -> list:
    if USE_DB:
        q = ActivityEntry.query.order_by(ActivityEntry.timestamp.asc())
        if agent_filter:
            q = q.filter(ActivityEntry.agent == agent_filter)
        if since:
            since_dt = _parse_dt(since)
            if since_dt:
                q = q.filter(ActivityEntry.timestamp > since_dt)
        total = q.count()
        rows = q.offset(max(0, total - limit)).all()
        return [r.to_dict() for r in rows]

    entries = _read_jsonl("activity-feed.jsonl", 500)
    if agent_filter:
        entries = [e for e in entries if e.get("agent") == agent_filter]
    if since:
        try:
            since_dt = datetime.datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ")
            entries = [e for e in entries if _parse_dt(e.get("timestamp", "")) and _parse_dt(e["timestamp"]) > since_dt]
        except ValueError:
            pass
    return entries[-limit:]


def storage_activity_count() -> int:
    """Total activity entries — used by SSE stream to detect new entries."""
    if USE_DB:
        return ActivityEntry.query.count()
    path = _data_path("activity-feed.jsonl")
    if not os.path.exists(path):
        return 0
    with open(path, "r") as f:
        return sum(1 for line in f if line.strip())


def storage_get_activity_since_count(last_count: int) -> list:
    """Get entries added after the last known count — for SSE streaming."""
    if USE_DB:
        total = ActivityEntry.query.count()
        if total <= last_count:
            return []
        rows = ActivityEntry.query.order_by(ActivityEntry.id.asc()).offset(last_count).all()
        return [r.to_dict() for r in rows]

    entries = _read_jsonl("activity-feed.jsonl", 1000)
    if len(entries) <= last_count:
        return []
    return entries[last_count:]


# ── Meeting Room ──────────────────────────────────────────────────────────────

def storage_get_messages(room_id: str, limit: int = 60) -> list:
    if USE_DB:
        q = (MeetingMessage.query
             .filter(MeetingMessage.room_id == room_id)
             .order_by(MeetingMessage.timestamp.asc()))
        total = q.count()
        rows = q.offset(max(0, total - limit)).all()
        return [r.to_dict() for r in rows]

    return _read_jsonl(os.path.join("transcripts", f"{room_id}.jsonl"), limit)


def storage_save_message(room_id: str, msg: dict) -> dict:
    if USE_DB:
        row = MeetingMessage(
            room_id=room_id,
            timestamp=_parse_dt(msg.get("timestamp")) or datetime.datetime.utcnow(),
            from_agent=msg.get("from", "user"),
            to_agent=msg.get("to", "all"),
            message=msg.get("message", ""),
            msg_type=msg.get("type", "message"),
        )
        db.session.add(row)
        db.session.commit()
        return row.to_dict()

    transcript_path = _data_path(os.path.join("transcripts", f"{room_id}.jsonl"))
    os.makedirs(os.path.dirname(transcript_path), exist_ok=True)
    with open(transcript_path, "a") as f:
        f.write(json.dumps(msg) + "\n")
    return msg


# ── Blockers ──────────────────────────────────────────────────────────────────

def storage_get_blockers() -> dict:
    if USE_DB:
        rows = BlockerScan.query.order_by(BlockerScan.detected_at.desc()).all()
        return {"history": [r.to_dict() for r in rows]}

    return _read_json("blockers.json") or {"history": []}


def storage_save_blocker(entry: dict) -> dict:
    if USE_DB:
        row = BlockerScan(
            detected_at=_parse_dt(entry.get("detected_at")) or datetime.datetime.utcnow(),
            source=entry.get("source", "manual"),
            blockers=entry.get("blockers", []),
            notes=entry.get("notes"),
        )
        db.session.add(row)
        db.session.commit()
        return row.to_dict()

    data = _read_json("blockers.json") or {"history": []}
    data["history"].append(entry)
    _write_json("blockers.json", data)
    return entry


# ── Credits ───────────────────────────────────────────────────────────────────

def storage_get_credits() -> dict:
    # Credits are still read from JSON (updated by external scripts)
    return _read_json("credit-status.json") or {"providers": {}, "lastChecked": None}
