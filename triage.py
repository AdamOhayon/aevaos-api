"""
triage.py — AevaOS Dispatch & Triage Engine
============================================
Classifies incoming tasks, selects the right agent + model,
builds context-aware prompts, and executes via the right API.

API routing strategy:
  - Clara  → OpenAI API directly (OPENAI_API_KEY)  — codex-mini-latest
  - Others → OpenRouter (OPENROUTER_API_KEY)       — claude/gemini/etc.

Fallback chain (when primary fails):
  1. Primary model fails → try secondary model on same API
  2. OpenRouter out of credits → try Anthropic API directly (ANTHROPIC_API_KEY)
  3. All fail → return structured error

Flow:
  1. classify_task(message, context) → TaskClassification
  2. build_agent_prompt(classification, message, context) → (system, user)
  3. call_* → response text
  4. Return AgentResponse
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
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

# ---------------------------------------------------------------------------
# Agent model configuration
#
# Clara routes directly to OpenAI (OPENAI_API_KEY).
# All other agents route through OpenRouter (OPENROUTER_API_KEY).
# When an agent needs a "router" key for OR routing, set use_openai=False.
# ---------------------------------------------------------------------------

AGENT_CONFIG = {
    "aeva": {
        "primary_model":   "anthropic/claude-sonnet-4-5",
        "fallback_model":  "anthropic/claude-haiku-4-5",
        "use_openai":      False,   # → OpenRouter
    },
    "clara": {
        "primary_model":   "codex-mini-latest",
        "fallback_model":  "gpt-4.1",           # direct OpenAI fallback
        "use_openai":      True,    # → direct OpenAI API
    },
    "pixel": {
        "primary_model":   "google/gemini-2.5-pro-preview",
        "fallback_model":  "google/gemini-2.0-flash-001",
        "use_openai":      False,   # → OpenRouter
    },
    "sage": {
        "primary_model":   "anthropic/claude-haiku-4-5",
        "fallback_model":  "anthropic/claude-sonnet-4-5",
        "use_openai":      False,   # → OpenRouter
    },
}

# These are the model strings exposed in API responses / agent registry UI
AGENT_MODELS      = {k: v["primary_model"]  for k, v in AGENT_CONFIG.items()}
AGENT_FALLBACK_MODELS = {k: v["fallback_model"] for k, v in AGENT_CONFIG.items()}

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

# Anthropic direct API — used as last-resort fallback when OpenRouter is out
ANTHROPIC_FALLBACK_MODEL = "claude-3-5-haiku-20241022"


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
    status: str             # success|success_fallback|dry_run|error
    api_used: str           # openai|openrouter|anthropic_direct
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Classification engine
# ---------------------------------------------------------------------------

def classify_task(message: str, context: dict = None) -> TaskClassification:
    """
    Analyse the message and return a TaskClassification.
    Uses keyword density scoring per type.
    """
    text = message.lower()
    context = context or {}

    type_scores = {}
    matched_signals = {}
    for task_type, keywords in TASK_SIGNALS.items():
        hits = [kw for kw in keywords if kw in text]
        score = len(hits) / max(len(keywords), 1)
        type_scores[task_type] = score
        matched_signals[task_type] = hits

    best_type = max(type_scores, key=type_scores.get)
    best_score = type_scores[best_type]

    if best_score < 0.02:
        best_type = "general"
        best_score = 0.5

    confidence = min(1.0, best_score * 8)

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

    agent = TYPE_TO_AGENT.get(best_type, "aeva")
    if best_type == "research" and complexity == "high":
        agent = "aeva"

    model = AGENT_CONFIG[agent]["primary_model"]

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
        "(deep space dark theme, neon accents, glassmorphism, Tailwind CSS). "
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
    context = context or {}
    agent = classification.agent
    system = AGENT_SYSTEM_PROMPTS.get(agent, AGENT_SYSTEM_PROMPTS["aeva"])

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
    user_message = f"{ctx_block}\n\n---\n\n{message}" if ctx_block else message
    return system, user_message


# ---------------------------------------------------------------------------
# API callers
# ---------------------------------------------------------------------------

def call_openai(model: str, system_prompt: str, user_message: str, api_key: str) -> str:
    """
    Direct OpenAI Chat Completions call.
    Used by Clara with OPENAI_API_KEY for Codex models.
    """
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "max_completion_tokens": 8192,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    choices = result.get("choices", [])
    if not choices:
        raise ValueError(f"No choices in OpenAI response: {result}")
    return choices[0]["message"]["content"]


def call_openrouter(model: str, system_prompt: str, user_message: str, api_key: str) -> str:
    """
    OpenRouter chat completions call.
    Used by Aeva, Pixel, Sage (and Clara fallback if OpenAI fails).
    """
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "temperature": 0.7,
        "max_tokens": 4096,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://aeva.os",
            "X-Title":       "AevaOS Dispatch",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    choices = result.get("choices", [])
    if not choices:
        raise ValueError(f"No choices in OpenRouter response: {result}")
    return choices[0]["message"]["content"]


def call_anthropic_direct(model: str, system_prompt: str, user_message: str, api_key: str) -> str:
    """
    Direct Anthropic Messages API call.
    Used as a last-resort fallback when OpenRouter has no credits.
    """
    payload = json.dumps({
        "model":      model,
        "max_tokens": 4096,
        "system":     system_prompt,
        "messages": [
            {"role": "user", "content": user_message},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    content = result.get("content", [])
    if not content:
        raise ValueError(f"No content in Anthropic response: {result}")
    return content[0].get("text", "")


def _is_credit_error(exc: Exception) -> bool:
    """Return True if the exception looks like an out-of-credits / 402 error."""
    msg = str(exc).lower()
    return any(x in msg for x in ["402", "insufficient", "credit", "payment", "quota", "balance"])


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------

def dispatch(message: str, context: dict = None, thread_id: str = None,
             openrouter_key: str = None, openai_key: str = None,
             anthropic_key: str = None) -> AgentResponse:
    """
    Full dispatch pipeline:
      1. Classify task
      2. Build context-aware prompt
      3. Route to the correct API:
         - Clara → OpenAI direct (codex-mini-latest)
         - Others → OpenRouter
      4. On failure, try fallback model on the same API
      5. If OpenRouter out of credits → Anthropic direct (backup)
      6. Return structured AgentResponse

    Keys default to env vars if not passed explicitly.
    """
    openrouter_key  = openrouter_key  or os.environ.get("OPENROUTER_API_KEY",  "")
    openai_key      = openai_key      or os.environ.get("OPENAI_API_KEY",      "")
    anthropic_key   = anthropic_key   or os.environ.get("ANTHROPIC_API_KEY",   "")

    thread_id   = thread_id or str(uuid.uuid4())
    dispatch_id = str(uuid.uuid4())
    context     = context or {}

    # 1. Classify
    classification = classify_task(message, context)
    agent  = classification.agent
    config = AGENT_CONFIG[agent]

    # 2. Build prompts
    system_prompt, user_message = build_agent_prompt(classification, message, context)

    primary_model    = config["primary_model"]
    fallback_model   = config["fallback_model"]
    use_openai_direct = config["use_openai"]

    # 3. Execute with fallback chain
    start = time.time()
    response_text = ""
    status   = "error"
    api_used = "none"
    error    = None

    # ── DRY RUN when no keys available ──────────────────────────────────────
    if not openrouter_key and not (use_openai_direct and openai_key):
        response_text = (
            f"[DRY RUN — no API keys configured]\n\n"
            f"Would dispatch to **{agent}** ({primary_model})\n"
            f"Task type: {classification.task_type} | "
            f"Complexity: {classification.complexity} | "
            f"Confidence: {classification.confidence}"
        )
        status   = "dry_run"
        api_used = "none"
        error    = None

    # ── CLARA: direct OpenAI path ────────────────────────────────────────────
    elif use_openai_direct and openai_key:
        try:
            response_text = call_openai(primary_model, system_prompt, user_message, openai_key)
            status   = "success"
            api_used = "openai"
            classification.model = primary_model
        except Exception as e1:
            error = str(e1)
            # Fallback: try secondary OpenAI model
            try:
                response_text = call_openai(fallback_model, system_prompt, user_message, openai_key)
                status   = "success_fallback"
                api_used = "openai"
                classification.model = fallback_model
                error = None
            except Exception as e2:
                error = f"Primary: {e1} | Fallback: {e2}"
                response_text = f"[Clara error — OpenAI unreachable: {error}]"

    # ── ALL OTHERS: OpenRouter path ──────────────────────────────────────────
    else:
        if not openrouter_key:
            response_text = "[No OPENROUTER_API_KEY configured]"
            status   = "error"
            api_used = "none"
        else:
            try:
                response_text = call_openrouter(primary_model, system_prompt, user_message, openrouter_key)
                status   = "success"
                api_used = "openrouter"
                classification.model = primary_model
            except Exception as e1:
                error = str(e1)

                # Fallback A: try fallback model on OpenRouter
                try:
                    response_text = call_openrouter(fallback_model, system_prompt, user_message, openrouter_key)
                    status   = "success_fallback"
                    api_used = "openrouter"
                    classification.model = fallback_model
                    error    = None
                except Exception as e2:
                    # Fallback B: if credit/quota issue → use Anthropic direct
                    if (_is_credit_error(e1) or _is_credit_error(e2)) and anthropic_key:
                        try:
                            response_text = call_anthropic_direct(
                                ANTHROPIC_FALLBACK_MODEL,
                                system_prompt,
                                user_message,
                                anthropic_key,
                            )
                            status   = "success_anthropic_direct"
                            api_used = "anthropic_direct"
                            classification.model = ANTHROPIC_FALLBACK_MODEL
                            error    = None
                        except Exception as e3:
                            error = f"OR primary: {e1} | OR fallback: {e2} | Anthropic direct: {e3}"
                            response_text = f"[All APIs failed: {error}]"
                    else:
                        error = f"Primary: {e1} | Fallback: {e2}"
                        response_text = f"[OpenRouter error: {error}]"

    latency_ms = int((time.time() - start) * 1000)

    return AgentResponse(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        agent=agent,
        model=classification.model,
        classification=asdict(classification),
        response=response_text,
        latency_ms=latency_ms,
        status=status,
        api_used=api_used,
        error=error,
    )
