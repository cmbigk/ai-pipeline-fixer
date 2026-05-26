import os
import subprocess
from typing import Tuple

def _run_git(repo_path: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run a git command in the target repo and return the result."""
    return subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )


def create_branch(repo_path: str, branch_name: str, base_branch: str = "main") -> Tuple[bool, str, str]:
    """
    Checkout base_branch, pull latest, and create a new branch.
    Returns (success, actual_branch_name, message).
    """
    # Checkout base branch first
    result = _run_git(repo_path, ["checkout", base_branch])
    if result.returncode != 0:
        return False, "", f"Failed to checkout {base_branch}: {result.stderr}"

    # Pull latest
    result = _run_git(repo_path, ["pull", "origin", base_branch])
    if result.returncode != 0:
        return False, "", f"Failed to pull latest {base_branch}: {result.stderr}"

    # Create and switch to new branch
    result = _run_git(repo_path, ["checkout", "-b", branch_name])
    if result.returncode != 0:
        # If branch already exists, try with a suffix
        retry_branch = f"{branch_name}-retry"
        result = _run_git(repo_path, ["checkout", "-b", retry_branch])
        if result.returncode != 0:
            return False, "", f"Failed to create branch: {result.stderr}"
        return True, retry_branch, f"Branch '{retry_branch}' created (original name was taken)"

    return True, branch_name, f"Branch '{branch_name}' created"


def apply_file_changes(repo_path: str, files_changed: list[dict]) -> Tuple[bool, str]:
    """
    Write the new file contents to disk.
    files_changed: [{"path": "...", "action": "modify", "new_content": "..."}, ...]
    Returns (success, message).
    """
    applied = []
    for file_entry in files_changed:
        rel_path = file_entry["path"]
        new_content = file_entry["new_content"]
        full_path = os.path.join(repo_path, rel_path)

        # Create parent directories if needed
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        applied.append(rel_path)

    return True, f"Applied changes to: {', '.join(applied)}"


def generate_diff(repo_path: str) -> Tuple[str, str]:
    """
    Run git diff to see what changed.
    Returns (diff_stat, full_diff).
    """
    stat_result = _run_git(repo_path, ["diff", "--stat"])
    diff_result = _run_git(repo_path, ["diff"])
    return stat_result.stdout, diff_result.stdout


def commit_and_push(
    repo_path: str,
    branch_name: str,
    commit_message: str,
    files_to_stage: list[str],
) -> Tuple[bool, str]:
    """
    Stage only the specified files, commit, and push.
    Returns (success, message).
    """
    # Stage only validated changed files (not git add .)
    for file_path in files_to_stage:
        result = _run_git(repo_path, ["add", file_path])
        if result.returncode != 0:
            return False, f"Failed to stage '{file_path}': {result.stderr} {result.stdout}"

    # Commit
    result = _run_git(repo_path, ["commit", "-m", commit_message])
    if result.returncode != 0:
        return False, f"Failed to commit: {result.stderr} {result.stdout}"

    # Push
    result = _run_git(repo_path, ["push", "-u", "origin", branch_name])
    if result.returncode != 0:
        return False, f"Failed to push: {result.stderr} {result.stdout}"

    return True, f"Committed and pushed to '{branch_name}'"


def cleanup(repo_path: str, base_branch: str = "main") -> Tuple[bool, str]:
    """Return to base branch after the fix is done."""
    result = _run_git(repo_path, ["checkout", base_branch])
    if result.returncode != 0:
        return False, f"Failed to checkout {base_branch}: {result.stderr} {result.stdout}"
    return True, f"Returned to {base_branch} branch"

def delete_local_branch(repo_path: str, branch_name: str, base_branch: str = "main") -> Tuple[bool, str]:
    """
    Checkout base_branch, pull latest, and delete the branch locally.
    Returns (success, message).
    """
    # Checkout base branch first
    result = _run_git(repo_path, ["checkout", base_branch])
    if result.returncode != 0:
        return False, f"Failed to checkout {base_branch} before deleting branch: {result.stderr}"

    # Delete branch locally
    result = _run_git(repo_path, ["branch", "-D", branch_name])
    if result.returncode != 0:
        return False, f"Failed to delete local branch '{branch_name}': {result.stderr}"

    return True, f"Local branch '{branch_name}' deleted successfully"
