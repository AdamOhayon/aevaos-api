"""
triage.py — AevaOS Dispatch & Triage Engine
============================================
Classifies incoming tasks, selects the right agent + model,
builds context-aware prompts, and calls OpenRouter to execute.

Flow:
  1. classify_task(message, context) → TaskClassification
  2. build_context_package(classification, message, context) → dict
  3. call_agent(package, api_key) → AgentResponse
  4. Everything logged to DispatchLog table
"""

import json
import os
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Task type signals (keyword → type mapping)
# ---------------------------------------------------------------------------

TASK_SIGNALS = {
    "coding": [
        "code", "bug", "fix", "function", "class", "api", "endpoint", "script",
        "build", "deploy", "git", "error", "exception", "test", "database",
        "schema", "query", "refactor", "implement", "debug", "crash", "import",
        "module", "package", "install", "dockerfile", "railway", "vercel",
        "python", "typescript", "javascript", "flask", "next", "react", "sql",
        "storage", "migration", "model", "route", "middleware", "auth"
    ],
    "design": [
        "design", "ui", "ux", "css", "component", "layout", "color", "style",
        "animation", "frontend", "figma", "responsive", "dark mode", "theme",
        "button", "card", "modal", "sidebar", "navbar", "icon", "font",
        "gradient", "shadow", "hover", "transition", "visual", "beautiful",
        "look", "feel", "pixel", "mockup", "wireframe", "prototype"
    ],
    "research": [
        "research", "find", "look up", "what is", "explain", "summarize",
        "article", "compare", "analysis", "investigate", "learn about",
        "how does", "why", "document", "write up", "report", "review",
        "benchmark", "competitive", "market", "trends", "best practice"
    ],
    "coordination": [
        "task", "assign", "project", "schedule", "plan", "organize",
        "priority", "deadline", "blocker", "status", "update", "meeting",
        "standup", "sprint", "milestone", "roadmap", "delegate"
    ]
}

# Agent → preferred model mapping (mirrors agents-registry.json)
AGENT_MODELS = {
    "aeva":     "anthropic/claude-sonnet-4-5",
    "clara":    "openai/o3",
    "pixel":    "google/gemini-2.5-pro-preview",
    "sage":     "anthropic/claude-haiku-4-5",
}

AGENT_FALLBACK_MODELS = {
    "aeva":  "anthropic/claude-opus-4-5",
    "clara": "openai/o4-mini",
    "pixel": "google/gemini-2.0-flash-001",
    "sage":  "anthropic/claude-sonnet-4-5",
}

TYPE_TO_AGENT = {
    "coding":       "clara",
    "design":       "pixel",
    "research":     "sage",
    "coordination": "aeva",
    "general":      "aeva",
}

COMPLEXITY_THRESHOLDS = {
    "low":    (0, 0.3),
    "medium": (0.3, 0.65),
    "high":   (0.65, 1.0),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TaskClassification:
    task_type: str          # coding|design|research|coordination|general
    complexity: str         # low|medium|high
    complexity_score: float # 0.0–1.0
    confidence: float       # 0.0–1.0
    agent: str
    model: str
    signals: list           # matched keywords


@dataclass
class AgentResponse:
    dispatch_id: str
    thread_id: str
    agent: str
    model: str
    classification: dict
    response: str
    latency_ms: int
    status: str             # success|error|needs_clarification
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Classification engine
# ---------------------------------------------------------------------------

def classify_task(message: str, context: dict = None) -> TaskClassification:
    """
    Analyse the message and return a TaskClassification.
    Uses keyword density scoring per type.
    Complexity is scored from message length + technical depth.
    """
    text = message.lower()
    context = context or {}

    # Score each type
    type_scores = {}
    matched_signals = {}
    for task_type, keywords in TASK_SIGNALS.items():
        hits = [kw for kw in keywords if kw in text]
        score = len(hits) / max(len(keywords), 1)
        type_scores[task_type] = score
        matched_signals[task_type] = hits

    # Pick best type
    best_type = max(type_scores, key=type_scores.get)
    best_score = type_scores[best_type]

    # If no strong signal, fall back to general → Aeva
    if best_score < 0.02:
        best_type = "general"
        best_score = 0.5

    confidence = min(1.0, best_score * 8)  # scale up to readable range

    # Complexity scoring
    words = len(text.split())
    has_multiple_tasks = bool(re.search(r'(\band\b.*\band\b|also|additionally|furthermore|\n[-*•])', text))
    has_code_refs = bool(re.search(r'`[^`]+`|\.py|\.ts|\.tsx|function|class |def |\bapi\b', text))
    is_ambiguous = best_score < 0.04

    complexity_score = min(1.0, (
        (words / 200) * 0.4 +
        (0.3 if has_multiple_tasks else 0) +
        (0.2 if has_code_refs else 0) +
        (0.1 if is_ambiguous else 0)
    ))

    if complexity_score < COMPLEXITY_THRESHOLDS["medium"][0]:
        complexity = "low"
    elif complexity_score < COMPLEXITY_THRESHOLDS["high"][0]:
        complexity = "medium"
    else:
        complexity = "high"

    # Escalate model for high complexity
    agent = TYPE_TO_AGENT.get(best_type, "aeva")

    # If high complexity research → upgrade to Sonnet
    if best_type == "research" and complexity == "high":
        agent = "aeva"

    model = AGENT_MODELS[agent]

    return TaskClassification(
        task_type=best_type,
        complexity=complexity,
        complexity_score=round(complexity_score, 3),
        confidence=round(confidence, 3),
        agent=agent,
        model=model,
        signals=matched_signals.get(best_type, [])[:8],
    )


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPTS = {
    "aeva": (
        "You are Aeva, the strategic orchestrator of AevaOS — an AI operating system "
        "for Adam. You are direct, thoughtful, and proactive. You help brainstorm, plan, "
        "and coordinate across agents. When handling a delegated task, be concise and "
        "actionable. If something is unclear, ask a targeted clarifying question. "
        "You continuously learn and improve — after responding, note any patterns "
        "or insights worth remembering in your response metadata."
    ),
    "clara": (
        "You are Clara, a senior software engineer in the AevaOS system. "
        "You receive focused coding tasks with relevant context (files, stack, constraints). "
        "Write clean, production-ready code. Be precise. Include brief explanation "
        "of what you built and any caveats. Stack: Python/Flask, TypeScript/Next.js, "
        "PostgreSQL via SQLAlchemy. Deployed on Railway (API) and Vercel (UI)."
    ),
    "pixel": (
        "You are Pixel, a UI/UX specialist in the AevaOS system. "
        "You receive design tasks with context about the existing visual system "
        "(dark theme: bg-gray-950, accent blue-600, glass cards, Tailwind CSS). "
        "Deliver precise CSS, component code, or design direction. Be visual and specific. "
        "Prefer subtle animations, glassmorphism, and micro-interactions."
    ),
    "sage": (
        "You are Sage, a research analyst in the AevaOS system. "
        "You receive research tasks with specific questions and scope. "
        "Be factual, concise, and structured. Format output as markdown "
        "with clear sections and key takeaways at the top. Cite reasoning."
    ),
}


def build_agent_prompt(classification: TaskClassification, message: str, context: dict = None) -> tuple:
    """
    Returns (system_prompt, user_message) for the chosen agent.
    Injects relevant context slices per agent type.
    """
    context = context or {}
    agent = classification.agent

    system = AGENT_SYSTEM_PROMPTS.get(agent, AGENT_SYSTEM_PROMPTS["aeva"])

    # Build user message with context injection
    ctx_lines = []

    if context.get("active_tasks"):
        ctx_lines.append(f"**Active tasks:** {json.dumps(context['active_tasks'][:3])}")
    if context.get("project"):
        ctx_lines.append(f"**Project:** {context['project']}")
    if context.get("relevant_files"):
        ctx_lines.append(f"**Relevant files:** {', '.join(context['relevant_files'])}")
    if context.get("thread_history"):
        ctx_lines.append(f"**Prior conversation:**\n{context['thread_history']}")
    if context.get("custom"):
        ctx_lines.append(f"**Context:** {context['custom']}")

    ctx_block = "\n".join(ctx_lines)
    if ctx_block:
        user_message = f"{ctx_block}\n\n---\n\n{message}"
    else:
        user_message = message

    return system, user_message


# ---------------------------------------------------------------------------
# OpenRouter API call
# ---------------------------------------------------------------------------

def call_openrouter(model: str, system_prompt: str, user_message: str, api_key: str) -> str:
    """
    Makes a chat completion call to OpenRouter.
    Returns the response text or raises on error.
    """
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://aeva.os",
            "X-Title": "AevaOS Dispatch",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    choices = result.get("choices", [])
    if not choices:
        raise ValueError(f"No choices in OpenRouter response: {result}")

    return choices[0]["message"]["content"]


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------

def dispatch(message: str, context: dict = None, thread_id: str = None, api_key: str = None) -> AgentResponse:
    """
    Full dispatch pipeline:
      1. Classify task
      2. Build context-aware prompt
      3. Call OpenRouter
      4. Return structured AgentResponse

    If api_key is None, reads from OPENROUTER_API_KEY env var.
    """
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    thread_id = thread_id or str(uuid.uuid4())
    dispatch_id = str(uuid.uuid4())
    context = context or {}

    # 1. Classify
    classification = classify_task(message, context)

    # 2. Build prompts
    system_prompt, user_message = build_agent_prompt(classification, message, context)

    # 3. Call OpenRouter (or return dry-run if no API key)
    start = time.time()
    try:
        if not api_key:
            response_text = (
                f"[DRY RUN — OPENROUTER_API_KEY not set]\n\n"
                f"Would dispatch to **{classification.agent}** ({classification.model})\n"
                f"Task type: {classification.task_type} | Complexity: {classification.complexity} | "
                f"Confidence: {classification.confidence}"
            )
            status = "dry_run"
        else:
            response_text = call_openrouter(
                model=classification.model,
                system_prompt=system_prompt,
                user_message=user_message,
                api_key=api_key,
            )
            status = "success"
        error = None
    except Exception as e:
        response_text = f"[Error calling {classification.model}: {e}]"
        status = "error"
        error = str(e)

        # Try fallback model
        if api_key and classification.agent in AGENT_FALLBACK_MODELS:
            fallback = AGENT_FALLBACK_MODELS[classification.agent]
            try:
                response_text = call_openrouter(
                    model=fallback,
                    system_prompt=system_prompt,
                    user_message=user_message,
                    api_key=api_key,
                )
                classification.model = fallback  # record actual model used
                status = "success_fallback"
                error = None
            except Exception as fe:
                error = f"Primary: {e} | Fallback: {fe}"

    latency_ms = int((time.time() - start) * 1000)

    return AgentResponse(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        agent=classification.agent,
        model=classification.model,
        classification=asdict(classification),
        response=response_text,
        latency_ms=latency_ms,
        status=status,
        error=error,
    )
