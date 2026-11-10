"""Long-term memory the assistant controls itself."""
from __future__ import annotations

from datetime import datetime

from . import N, S, registry, tool


@tool(
    "Save a fact to long-term memory so it is known in every future conversation: the user's preferences, "
    "people, projects, where they keep things, how they like tasks done. Write it as one self-contained sentence.",
    {"fact": S("The fact to remember")},
)
def remember(fact: str):
    fid = registry.ctx.memory.remember(fact)
    return f"Remembered (id {fid})."


@tool(
    "Remove a fact from long-term memory by its id (ids are shown next to each fact in your memory list).",
    {"fact_id": N("Id of the fact to forget")},
)
def forget(fact_id: float):
    ok = registry.ctx.memory.forget(int(fact_id))
    return "Forgotten." if ok else "No fact with that id."


@tool(
    "Search earlier conversations and saved facts for some text, to recall what was said or done before "
    "(beyond the recent turns you already see).",
    {"query": S("Word or phrase to look for")},
)
def recall(query: str):
    mem = registry.ctx.memory
    facts = [f"fact {i}: {t}" for i, t in mem.search_facts(query)]
    turns = [f"{datetime.fromtimestamp(ts):%Y-%m-%d %H:%M} {role}: {text[:300]}" for ts, role, text in mem.search_log(query)]
    return "\n".join(facts + turns) or "Nothing found."
