"""
AevaOS — SQLAlchemy models for PostgreSQL backend (v1.4.0)
Falls back to JSON files when DATABASE_URL is not set.
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Task(db.Model):
    __tablename__ = "tasks"

    id            = db.Column(db.String(50),  primary_key=True)
    title         = db.Column(db.Text,        nullable=False)
    description   = db.Column(db.Text,        default="")
    project       = db.Column(db.String(100), default="aeva-os")
    status        = db.Column(db.String(50),  default="ready", index=True)
    assignee      = db.Column(db.String(100), default="", index=True)
    urgency       = db.Column(db.String(50),  default="medium")
    impact        = db.Column(db.String(50),  default="medium")
    effort        = db.Column(db.String(50),  default="medium")
    complexity    = db.Column(db.String(50),  default="medium")
    priority      = db.Column(db.String(50),  default="medium")
    priority_score = db.Column(db.Integer,    default=0)
    is_blocked    = db.Column(db.Boolean,     default=False)
    days_stuck    = db.Column(db.Integer,     default=0)
    created_at    = db.Column(db.DateTime,    default=datetime.utcnow)
    created_by    = db.Column(db.String(100), default="ui")
    started_at    = db.Column(db.DateTime,    nullable=True)
    completed_at  = db.Column(db.DateTime,    nullable=True)
    last_updated  = db.Column(db.DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)
    activity_log  = db.Column(db.JSON,        default=list)
    notes         = db.Column(db.JSON,        default=list)
    meta          = db.Column(db.JSON,        default=dict)  # _score_breakdown, etc.

    def to_dict(self):
        return {
            "id":           self.id,
            "title":        self.title,
            "description":  self.description,
            "project":      self.project,
            "status":       self.status,
            "assignee":     self.assignee,
            "urgency":      self.urgency,
            "impact":       self.impact,
            "effort":       self.effort,
            "complexity":   self.complexity,
            "priority":     self.priority,
            "priority_score": self.priority_score,
            "is_blocked":   self.is_blocked,
            "days_stuck":   self.days_stuck,
            "createdAt":    self.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.created_at else None,
            "createdBy":    self.created_by,
            "startedAt":    self.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.started_at else None,
            "completedAt":  self.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.completed_at else None,
            "last_updated": self.last_updated.strftime("%Y-%m-%dT%H:%M:%SZ") if self.last_updated else None,
            "activity_log": self.activity_log or [],
            "notes":        self.notes or [],
        }


class Idea(db.Model):
    __tablename__ = "ideas"

    id          = db.Column(db.String(50),  primary_key=True)
    title       = db.Column(db.Text,        nullable=False)
    description = db.Column(db.Text,        default="")
    category    = db.Column(db.String(50),  default="research", index=True)
    status      = db.Column(db.String(50),  default="new", index=True)
    source      = db.Column(db.String(100), default="api")
    captured_at = db.Column(db.DateTime,    default=datetime.utcnow)
    tags        = db.Column(db.JSON,        default=list)
    notion_url  = db.Column(db.Text,        nullable=True)

    def to_dict(self):
        return {
            "id":          self.id,
            "title":       self.title,
            "description": self.description,
            "category":    self.category,
            "status":      self.status,
            "source":      self.source,
            "capturedAt":  self.captured_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.captured_at else None,
            "tags":        self.tags or [],
            "notionUrl":   self.notion_url,
        }


class Project(db.Model):
    __tablename__ = "projects"

    id            = db.Column(db.String(50),  primary_key=True)
    name          = db.Column(db.Text,        nullable=False)
    description   = db.Column(db.Text,        default="")
    status        = db.Column(db.String(50),  default="active", index=True)
    priority      = db.Column(db.Integer,     default=99)
    github        = db.Column(db.String(200), nullable=True)
    health        = db.Column(db.String(20),  default="unknown")
    last_activity = db.Column(db.DateTime,    nullable=True)
    meta          = db.Column(db.JSON,        default=dict)

    def to_dict(self):
        return {
            "id":           self.id,
            "name":         self.name,
            "description":  self.description,
            "status":       self.status,
            "priority":     self.priority,
            "github":       self.github,
            "health":       self.health,
            "lastActivity": self.last_activity.strftime("%Y-%m-%dT%H:%M:%SZ") if self.last_activity else None,
        }


class Agent(db.Model):
    __tablename__ = "agents"

    id            = db.Column(db.String(100), primary_key=True)
    name          = db.Column(db.String(100), nullable=False)
    emoji         = db.Column(db.String(10),  default="🤖")
    role          = db.Column(db.String(200), default="AI Agent")
    status        = db.Column(db.String(50),  default="idle", index=True)
    current_task  = db.Column(db.Text,        nullable=True)
    current_project = db.Column(db.String(100), nullable=True)
    current_model = db.Column(db.String(100), nullable=True)
    session_key   = db.Column(db.String(200), nullable=True)
    last_seen     = db.Column(db.DateTime,    nullable=True)
    last_activity = db.Column(db.Text,        nullable=True)
    capabilities  = db.Column(db.JSON,        default=list)
    preferences   = db.Column(db.JSON,        default=dict)
    metrics       = db.Column(db.JSON,        default=dict)

    def to_dict(self):
        return {
            "id":             self.id,
            "name":           self.name,
            "emoji":          self.emoji,
            "role":           self.role,
            "status":         self.status,
            "currentTask":    self.current_task,
            "currentProject": self.current_project,
            "currentModel":   self.current_model,
            "sessionKey":     self.session_key,
            "lastSeen":       self.last_seen.strftime("%Y-%m-%dT%H:%M:%SZ") if self.last_seen else None,
            "lastActivity":   self.last_activity,
            "capabilities":   self.capabilities or [],
            "preferences":    self.preferences or {},
            "metrics":        self.metrics or {},
        }


class ActivityEntry(db.Model):
    __tablename__ = "activity_feed"

    id        = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    timestamp = db.Column(db.DateTime,    default=datetime.utcnow, index=True)
    agent     = db.Column(db.String(100), default="system")
    action    = db.Column(db.String(100), default="note")
    message   = db.Column(db.Text,        default="")
    meta      = db.Column(db.JSON,        default=dict)

    def to_dict(self):
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if self.timestamp else None,
            "agent":     self.agent,
            "action":    self.action,
            "message":   self.message,
            "metadata":  self.meta or {},
        }


class MeetingMessage(db.Model):
    __tablename__ = "meeting_messages"

    id         = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    room_id    = db.Column(db.String(100), default="main-office", index=True)
    timestamp  = db.Column(db.DateTime,    default=datetime.utcnow, index=True)
    from_agent = db.Column(db.String(100), default="user")
    to_agent   = db.Column(db.String(100), default="all")
    message    = db.Column(db.Text,        nullable=False)
    msg_type   = db.Column(db.String(50),  default="message")

    def to_dict(self):
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if self.timestamp else None,
            "from":      self.from_agent,
            "to":        self.to_agent,
            "message":   self.message,
            "type":      self.msg_type,
        }


class BlockerScan(db.Model):
    __tablename__ = "blocker_scans"

    id          = db.Column(db.Integer,  primary_key=True, autoincrement=True)
    detected_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    source      = db.Column(db.String(100), default="manual")
    blockers    = db.Column(db.JSON,     default=list)
    notes       = db.Column(db.Text,     nullable=True)

    def to_dict(self):
        return {
            "detected_at": self.detected_at.strftime("%Y-%m-%dT%H:%M:%SZ") if self.detected_at else None,
            "source":      self.source,
            "blockers":    self.blockers or [],
        }
