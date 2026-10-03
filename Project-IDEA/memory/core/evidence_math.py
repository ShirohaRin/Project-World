from __future__ import annotations

from datetime import datetime

EVIDENCE_CONFIRMED_THRESHOLD = 1.0
EVIDENCE_PROMOTED_THRESHOLD = 2.0
EVIDENCE_ARCHIVE_THRESHOLD = -2.0
EVIDENCE_REIN_HALF_LIFE_DAYS = 30
EVIDENCE_DISP_HALF_LIFE_DAYS = 180
USER_FACT_REINFORCE_COMBO_THRESHOLD = 2
USER_FACT_REINFORCE_COMBO_BONUS = 0.5


def _age_days(timestamp: str | None, now: datetime) -> float:
    if not timestamp:
        return 0.0
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (now - parsed).total_seconds() / 86400)


def initial_reinforcement_from_importance(max_importance: int) -> float:
    try:
        importance = int(max_importance)
    except (TypeError, ValueError):
        return 0.0
    for threshold, seed in ((10, 0.8), (9, 0.6), (8, 0.4), (7, 0.2)):
        if importance >= threshold:
            return seed
    return 0.0


def effective_reinforcement(entry: dict, now: datetime) -> float:
    value = float(entry.get("reinforcement", 0.0) or 0.0)
    return value * (0.5 ** (_age_days(entry.get("rein_last_signal_at"), now) / EVIDENCE_REIN_HALF_LIFE_DAYS))


def effective_disputation(entry: dict, now: datetime) -> float:
    value = float(entry.get("disputation", 0.0) or 0.0)
    return value * (0.5 ** (_age_days(entry.get("disp_last_signal_at"), now) / EVIDENCE_DISP_HALF_LIFE_DAYS))


def evidence_score(entry: dict, now: datetime) -> float:
    if entry.get("protected"):
        return float("inf")
    return effective_reinforcement(entry, now) - effective_disputation(entry, now)


def derive_status(entry: dict, now: datetime) -> str:
    score = evidence_score(entry, now)
    if score >= EVIDENCE_PROMOTED_THRESHOLD:
        return "promoted"
    if score >= EVIDENCE_CONFIRMED_THRESHOLD:
        return "confirmed"
    if score <= EVIDENCE_ARCHIVE_THRESHOLD:
        return "archive_candidate"
    return "pending"


def compute_evidence_snapshot(entry: dict, delta: dict, now_iso: str, source: str) -> dict:
    reinforcement_delta = float(delta.get("reinforcement", 0.0) or 0.0)
    disputation_delta = float(delta.get("disputation", 0.0) or 0.0)
    reinforcement = float(entry.get("reinforcement", 0.0) or 0.0) + reinforcement_delta
    disputation = max(0.0, float(entry.get("disputation", 0.0) or 0.0) + disputation_delta)
    count = int(entry.get("user_fact_reinforce_count", 0) or 0)
    if source == "user_fact" and reinforcement_delta > 0:
        count += 1
        if count > USER_FACT_REINFORCE_COMBO_THRESHOLD:
            reinforcement += USER_FACT_REINFORCE_COMBO_BONUS
    return {
        "reinforcement": reinforcement,
        "disputation": disputation,
        "rein_last_signal_at": now_iso if reinforcement_delta else entry.get("rein_last_signal_at"),
        "disp_last_signal_at": now_iso if disputation_delta else entry.get("disp_last_signal_at"),
        "sub_zero_days": int(entry.get("sub_zero_days", 0) or 0),
        "user_fact_reinforce_count": count,
    }


def maybe_mark_sub_zero(entry: dict, now: datetime) -> bool:
    if entry.get("protected") or evidence_score(entry, now) >= 0:
        return False
    today = now.date().isoformat()
    if entry.get("sub_zero_last_increment_date") == today:
        return False
    entry["sub_zero_days"] = int(entry.get("sub_zero_days", 0) or 0) + 1
    entry["sub_zero_last_increment_date"] = today
    return True
