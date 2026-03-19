"""
seed_db.py — One-time migration from JSON files → PostgreSQL

Usage:
    DATABASE_URL=postgresql://... python seed_db.py

Safe to run multiple times — skips records that already exist by primary key.
"""
import os, json, sys, datetime

# ── Setup Flask app context ───────────────────────────────────────────────────
from app import app
from models import db, Task, Idea, Project, Agent, ActivityEntry, MeetingMessage, BlockerScan

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _data_path(f):
    return os.path.join(DATA_DIR, f)


def _read_json(f):
    p = _data_path(f)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def _read_jsonl(f, limit=10000):
    p = _data_path(f)
    if not os.path.exists(p):
        return []
    with open(p) as fh:
        lines = fh.readlines()
    out = []
    for line in lines[:limit]:
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def seed_tasks():
    data = _read_json("tasks.json") or {}
    tasks = data.get("tasks", [])
    added = 0
    for t in tasks:
        if Task.query.get(t["id"]):
            continue
        row = Task(
            id=t["id"], title=t.get("title", ""), description=t.get("description", ""),
            project=t.get("project", "aeva-os"), status=t.get("status", "ready"),
            assignee=t.get("assignee", ""), urgency=t.get("urgency", "medium"),
            impact=t.get("impact", "medium"), effort=t.get("effort", "medium"),
            complexity=t.get("complexity", "medium"),
            priority=t.get("priority", t.get("urgency", "medium")),
            priority_score=t.get("priority_score", 0),
            is_blocked=t.get("is_blocked", False), days_stuck=t.get("days_stuck", 0),
            created_at=_parse_dt(t.get("createdAt")),
            created_by=t.get("createdBy", "system"),
            completed_at=_parse_dt(t.get("completedAt")),
            started_at=_parse_dt(t.get("startedAt")),
            activity_log=t.get("activity_log", []),
            notes=t.get("notes", []),
        )
        db.session.add(row)
        added += 1
    db.session.commit()
    print(f"  tasks: {added} inserted ({len(tasks) - added} already existed)")


def seed_ideas():
    data = _read_json("ideas.json") or {}
    ideas = data.get("ideas", [])
    added = 0
    for i in ideas:
        if Idea.query.get(i["id"]):
            continue
        row = Idea(
            id=i["id"], title=i.get("title", ""), description=i.get("description", ""),
            category=i.get("category", "research"), status=i.get("status", "new"),
            source=i.get("source", "api"),
            captured_at=_parse_dt(i.get("capturedAt")),
            tags=i.get("tags", []), notion_url=i.get("notionUrl"),
        )
        db.session.add(row)
        added += 1
    db.session.commit()
    print(f"  ideas: {added} inserted ({len(ideas) - added} already existed)")


def seed_projects():
    data = _read_json("projects.json") or {}
    projects = data.get("projects", [])
    added = 0
    for p in projects:
        if Project.query.get(p["id"]):
            continue
        row = Project(
            id=p["id"], name=p.get("name", ""), description=p.get("description", ""),
            status=p.get("status", "active"), priority=p.get("priority", 99),
            github=p.get("github"), health=p.get("health", "unknown"),
            last_activity=_parse_dt(p.get("lastActivity")),
        )
        db.session.add(row)
        added += 1
    db.session.commit()
    print(f"  projects: {added} inserted ({len(projects) - added} already existed)")


def seed_agents():
    data = _read_json("agents-registry.json") or {}
    agents = data.get("agents", {})
    added = 0
    for agent_id, a in agents.items():
        if Agent.query.get(agent_id):
            continue
        row = Agent(
            id=agent_id, name=a.get("name", agent_id), emoji=a.get("emoji", "🤖"),
            role=a.get("role", "AI Agent"), status=a.get("status", "idle"),
            current_task=a.get("currentTask"), current_project=a.get("currentProject"),
            current_model=a.get("model"), session_key=a.get("sessionKey"),
            last_seen=_parse_dt(a.get("lastSeen")),
            last_activity=a.get("lastActivity"),
            capabilities=a.get("capabilities", []),
            preferences=a.get("preferences", {}),
            metrics=a.get("metrics", {}),
        )
        db.session.add(row)
        added += 1
    db.session.commit()
    print(f"  agents: {added} inserted ({len(agents) - added} already existed)")


def seed_activity():
    entries = _read_jsonl("activity-feed.jsonl")
    added = 0
    for e in entries:
        row = ActivityEntry(
            timestamp=_parse_dt(e.get("timestamp")) or datetime.datetime.utcnow(),
            agent=e.get("agent", "system"), action=e.get("action", "note"),
            message=e.get("message", ""), meta=e.get("metadata", {}),
        )
        db.session.add(row)
        added += 1
    db.session.commit()
    print(f"  activity_feed: {added} inserted")


def seed_messages():
    import glob
    transcript_dir = os.path.join(DATA_DIR, "transcripts")
    if not os.path.exists(transcript_dir):
        print("  meeting_messages: no transcripts dir")
        return
    added = 0
    for jf in glob.glob(os.path.join(transcript_dir, "*.jsonl")):
        room_id = os.path.splitext(os.path.basename(jf))[0]
        with open(jf) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                    row = MeetingMessage(
                        room_id=room_id,
                        timestamp=_parse_dt(m.get("timestamp")) or datetime.datetime.utcnow(),
                        from_agent=m.get("from", "user"), to_agent=m.get("to", "all"),
                        message=m.get("message", ""), msg_type=m.get("type", "message"),
                    )
                    db.session.add(row)
                    added += 1
                except json.JSONDecodeError:
                    pass
    db.session.commit()
    print(f"  meeting_messages: {added} inserted")


def seed_blockers():
    data = _read_json("blockers.json") or {}
    history = data.get("history", [])
    added = 0
    for entry in history:
        row = BlockerScan(
            detected_at=_parse_dt(entry.get("detected_at")) or datetime.datetime.utcnow(),
            source=entry.get("source", "manual"),
            blockers=entry.get("blockers", []),
        )
        db.session.add(row)
        added += 1
    db.session.commit()
    print(f"  blockers: {added} inserted")


def main():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL not set. Aborting.")
        sys.exit(1)

    with app.app_context():
        print("Creating tables...")
        db.create_all()
        print("Seeding data from JSON files...")
        seed_tasks()
        seed_ideas()
        seed_projects()
        seed_agents()
        seed_activity()
        seed_messages()
        seed_blockers()
        print("✅ Seed complete!")


if __name__ == "__main__":
    main()
