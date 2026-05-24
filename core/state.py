import os
import json
from datetime import datetime, timezone
from typing import Optional

PROPOSALS_DIR = "proposals"

# Ensure proposals directory exists
os.makedirs(PROPOSALS_DIR, exist_ok=True)

# Valid statuses and allowed transitions
VALID_STATUSES = [
    "parsed",
    "proposal_ready",
    "pending_approval",
    "approved",
    "rejected",
    "executing_fix",
    "fixed",
    "pr_created",
    "failed",
]


def _file_path(job_id: str) -> str:
    return os.path.join(PROPOSALS_DIR, f"{job_id}.json")


def save_state(job_id: str, data: dict) -> None:
    """Write proposal state to a JSON file on disk."""
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(_file_path(job_id), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_state(job_id: str) -> Optional[dict]:
    """Read proposal state from disk. Returns None if not found."""
    path = _file_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_all_states() -> list[dict]:
    """List all proposal states, sorted by most recent first."""
    states = []
    for filename in os.listdir(PROPOSALS_DIR):
        if filename.endswith(".json"):
            path = os.path.join(PROPOSALS_DIR, filename)
            with open(path, "r", encoding="utf-8") as f:
                states.append(json.load(f))
    # Sort by created_at descending
    states.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return states


def create_initial_state(job_id: str, repo_full_name: str, parser_output: dict) -> dict:
    """Create a new proposal state after parsing."""
    data = {
        "job_id": str(job_id),
        "status": "parsed",
        "repo_full_name": repo_full_name,
        "parser_output": parser_output,
        "proposal": None,
        "raw_proposal_response": None,
        "execution_result": None,
        "raw_execution_response": None,
        "diff_summary": None,
        "pr_url": None,
        "error_message": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_state(str(job_id), data)
    return data


def update_status(job_id: str, new_status: str, **extra_fields) -> Optional[dict]:
    """Load state, update the status and any extra fields, save, and return it."""
    data = load_state(str(job_id))
    if data is None:
        return None
    data["status"] = new_status
    for key, value in extra_fields.items():
        data[key] = value
    save_state(str(job_id), data)
    return data
