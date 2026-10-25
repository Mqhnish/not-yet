"""The morning brief: Gemma writes it, Python fact-checks it.

The model only gets facts the scheduler computed, with every time already
written out. If the brief mentions a time that is not in those facts, or runs
long, we throw it away and use a plain template instead.
"""
from __future__ import annotations

import re

SYSTEM = "You write very short, calm, dry, kind messages in the voice of a sloth who is good at planning."

PROMPT = """Write a morning brief for a tired person from the facts below.

Rules:
- At most 3 short sentences, under 60 words in total.
- Use only these facts. Do not add tasks, advice, or times.
- Write every time exactly as it appears in the facts.
- Plain text. No lists, no markdown, at most one emoji.

Facts:
{facts}
"""

TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def facts_text(facts: list[str]) -> str:
    return "\n".join(f"- {f}" for f in facts)


def check(brief: str, facts: list[str]) -> str | None:
    """Returns the reason a brief is rejected, or None if it is fine."""
    brief = brief.strip()
    if not brief:
        return "empty"
    if len(brief.split()) > 80:
        return "too long"
    allowed = set(TIME_RE.findall(" ".join(facts)))
    bad = [t for t in TIME_RE.findall(brief) if t not in allowed]
    if bad:
        return f"mentions times that are not in the plan: {', '.join(bad)}"
    return None


def template(facts: dict) -> str:
    parts = []
    if facts.get("first"):
        parts.append(f"Nothing needs you until {facts['first']}, so rest.")
    else:
        parts.append("Nothing on this list needs scheduled work. Rest.")
    if facts.get("first_task"):
        parts.append(f"Then it's {facts['first_task']}.")
    if facts.get("short"):
        parts.append(f"One honest warning: {facts['short']}.")
    elif facts.get("do_now"):
        parts.append(f"Before you lie down, knock out {facts['do_now']}.")
    return " ".join(parts)


def write(llm, fact_lines: list[str], fallback: dict) -> tuple[str, str]:
    """Returns (brief, source) where source is "model" or "template"."""
    if llm is None:
        return template(fallback), "template"
    try:
        text = llm.complete(PROMPT.format(facts=facts_text(fact_lines)), system=SYSTEM, max_tokens=300, temperature=0.7)
    except Exception:
        return template(fallback), "template"
    text = re.sub(r"\s+", " ", text.replace("*", "")).strip().strip('"')
    if check(text, fact_lines) is None:
        return text, "model"
    return template(fallback), "template"
