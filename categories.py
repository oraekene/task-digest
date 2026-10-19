"""One table for money and consequence categories, shared by merge and apply.

A category only ever raises priority. Classification must not quietly demote a
task that a person or an earlier pass ranked higher.

Usage:
    import categories
    categories.apply(task, use_deadlines=False)
"""
from datetime import date

CATEGORY_TIERS = {
    "incoming-promised": (1, 1),
    "client-job": (1, 1),
    "money-machine": (1, 1),
    "blocks-work": (1, 1),
    "health-safety": (1, 1),
    "legal-account": (1, 2),
    "outgoing-due": (1, 2),
    "incoming-window": (1, 2),
    "promise": (2, 2),
    "to-save": (2, 2),
    "new-opportunity": (3, 3),
    "none": (None, None),
}

CATEGORY_IMPACT = {
    "incoming-promised": 0.4,
    "client-job": 0.4,
    "money-machine": 0.4,
    "blocks-work": 0.4,
    "health-safety": 0.4,
    "legal-account": 0.4,
    "outgoing-due": 0.3,
    "incoming-window": 0.3,
    "promise": 0.3,
    "to-save": 0.2,
    "new-opportunity": 0.2,
    "none": None,
}


def target_tier(task: dict, use_deadlines: bool) -> int | None:
    inside, outside = CATEGORY_TIERS.get(task.get("category") or "none", (None, None))
    if inside is None:
        return None
    deadline = task.get("deadline")
    if use_deadlines and deadline:
        days = (date.fromisoformat(str(deadline)[:10]) - date.today()).days
        return inside if days <= 7 else outside
    return outside


def apply(task: dict, use_deadlines: bool = False) -> bool:
    """Raise tier and impact to the category floor. Return True if changed."""
    if "demoted: stale" in (task.get("why") or ""):
        return False
    category = task.get("category") or "none"
    before = (task.get("tier"), task.get("impact"))
    tier = target_tier(task, use_deadlines)
    if tier is not None:
        task["tier"] = min(int(task.get("tier") or tier), tier)
    floor = CATEGORY_IMPACT.get(category)
    if floor is not None:
        task["impact"] = max(float(task.get("impact") or 0), floor)
    changed = (task.get("tier"), task.get("impact")) != before
    if changed and category != "none" and "money:" not in (task.get("why") or ""):
        task["why"] = ((task.get("why") or "").rstrip(" |") + f" | money: {category}").strip(" |")
    return changed