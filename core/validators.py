import os
import re
import yaml
from typing import Tuple

# All validators return (is_valid, error_message)
ValidationResult = Tuple[bool, str]


# --- AI Output Schema Validation ---

def validate_proposal_schema(data: dict) -> ValidationResult:
    """Check that a proposal response from the AI has all required fields and correct types."""
    required_fields = {
        "error_explanation": str,
        "suggested_fix": str,
        "is_patchable": bool,
        "candidate_files": list,
        "risk_level": str,
        "suggested_branch_name": str,
        "suggested_pr_title": str,
        "pr_description_summary": str,
    }
    for field, expected_type in required_fields.items():
        if field not in data:
            return False, f"Missing required field: '{field}'"
        if not isinstance(data[field], expected_type):
            return False, f"Field '{field}' must be {expected_type.__name__}, got {type(data[field]).__name__}"

    # candidate_files must be a list of strings
    for item in data["candidate_files"]:
        if not isinstance(item, str):
            return False, f"Each candidate_file must be a string, got {type(item).__name__}"

    # risk_level must be one of the allowed values
    allowed_risks = ["low", "medium", "high"]
    if data["risk_level"] not in allowed_risks:
        return False, f"risk_level must be one of {allowed_risks}, got '{data['risk_level']}'"

    return True, ""


def validate_execution_schema(data: dict) -> ValidationResult:
    """Check that an execution response from the AI has all required fields."""
    if "files_changed" not in data:
        return False, "Missing required field: 'files_changed'"
    if not isinstance(data["files_changed"], list):
        return False, "'files_changed' must be a list"
    if not data["files_changed"]:
        return False, "Execution result has no files_changed (list is empty). AI may have failed to generate a fix."
    if "change_summary" not in data:
        return False, "Missing required field: 'change_summary'"

    for i, file_entry in enumerate(data["files_changed"]):
        if not isinstance(file_entry, dict):
            return False, f"files_changed[{i}] must be a dict"
        for key in ["path", "action", "new_content"]:
            if key not in file_entry:
                return False, f"files_changed[{i}] missing required key: '{key}'"

    return True, ""


# --- Path Safety Checks ---

def validate_file_paths(paths: list[str], repo_path: str) -> ValidationResult:
    """Reject dangerous file paths: absolute, traversal, .git, outside repo."""
    repo_real = os.path.realpath(repo_path)

    for p in paths:
        # Reject absolute paths
        if os.path.isabs(p):
            return False, f"Absolute path not allowed: '{p}'"

        # Reject directory traversal
        if ".." in p.split(os.sep):
            return False, f"Directory traversal not allowed: '{p}'"

        # Reject .git directory
        if p.startswith(".git/") or p == ".git":
            return False, f"Cannot modify .git directory: '{p}'"

        # Verify resolved path stays inside the repo
        full_path = os.path.realpath(os.path.join(repo_path, p))
        if not full_path.startswith(repo_real):
            return False, f"Path escapes repo root: '{p}'"

    return True, ""


# --- Scope Enforcement ---

def validate_edit_scope(execution_result: dict, approved_candidates: list[str]) -> ValidationResult:
    """Ensure every file in the execution result was in the approved candidate list."""
    for file_entry in execution_result.get("files_changed", []):
        file_path = file_entry.get("path", "")
        if file_path not in approved_candidates:
            return False, f"AI edited file '{file_path}' which was not in the approved candidate list: {approved_candidates}"
    return True, ""


# --- Content Validation ---

def validate_file_content(files_changed: list[dict]) -> ValidationResult:
    """Reject empty content or suspiciously large changes."""
    MAX_LINES = 500  # MVP safety limit

    for file_entry in files_changed:
        content = file_entry.get("new_content", "")
        path = file_entry.get("path", "unknown")

        if not content or not content.strip():
            return False, f"File '{path}' has empty content"

        line_count = content.count("\n") + 1
        if line_count > MAX_LINES:
            return False, f"File '{path}' has {line_count} lines, exceeds limit of {MAX_LINES}"

    return True, ""


# --- Branch Name Safety ---

def validate_branch_name(name: str) -> ValidationResult:
    """Only allow safe branch name characters."""
    if not name:
        return False, "Branch name cannot be empty"
    if name.startswith("-"):
        return False, "Branch name cannot start with '-'"
    if not re.match(r'^[a-zA-Z0-9/_\-\.]+$', name):
        return False, f"Branch name contains invalid characters: '{name}'. Only [a-zA-Z0-9/_-.] allowed."
    return True, ""


# --- YAML Syntax Check ---

def validate_yaml_files(files_changed: list[dict]) -> ValidationResult:
    """For any .yml or .yaml file, verify it parses correctly."""
    for file_entry in files_changed:
        path = file_entry.get("path", "")
        if path.endswith(".yml") or path.endswith(".yaml"):
            content = file_entry.get("new_content", "")
            try:
                yaml.safe_load(content)
            except yaml.YAMLError as e:
                return False, f"YAML syntax error in '{path}': {e}"
    return True, ""


# --- Repo Clean Check ---

def validate_repo_clean(repo_path: str) -> ValidationResult:
    """Check that the target repo has no uncommitted changes."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip():
            return False, f"Target repo has uncommitted changes:\n{result.stdout.strip()}"
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, f"Failed to check repo status: {e.stderr}"
