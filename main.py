import os
import hmac
import hashlib
import json
import asyncio
from fastapi import FastAPI, Request, HTTPException, Header, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from core.github_client import GitHubClient
from core.log_parser import LogParser
from core import ai_agent
from core import state
from core import validators
from core import git_ops

# Load environment variables
load_dotenv()

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TARGET_REPO_PATH = os.getenv("TARGET_REPO_PATH", "")
DEFAULT_BRANCH = os.getenv("DEFAULT_BRANCH", "main")

app = FastAPI(title="AI Pipeline Fixer Webhook")
templates = Jinja2Templates(directory="templates")

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

github_client = GitHubClient(token=GITHUB_TOKEN)
log_parser = LogParser()

def verify_signature(payload_body: bytes, secret_token: str, signature_header: str) -> bool:
    """Verify that the payload was sent from GitHub by validating SHA256 signature."""
    if not signature_header:
        return False
    hash_object = hmac.new(secret_token.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)

@app.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(None),
    x_github_event: str = Header(None)
):
    # 1. Read payload
    payload_body = await request.body()
    
    # 2. Validate signature
    if not verify_signature(payload_body, GITHUB_WEBHOOK_SECRET, x_hub_signature_256):
        print("Invalid webhook signature received.")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 3. Check event type
    if x_github_event not in ["workflow_run", "pull_request"]:
        return {"status": "ignored", "reason": f"Unhandled event. Got: {x_github_event}"}

    payload = json.loads(payload_body)
    action = payload.get("action")

   # print(f"[WEBHOOK] Event: {x_github_event} | Action: {action}")

    # --- Handle PR Events ---
    if x_github_event == "pull_request":
        if action == "closed" and payload.get("pull_request", {}).get("merged") is True:
            pr = payload["pull_request"]
            head_branch = pr.get("head", {}).get("ref")
            if head_branch:
                print(f"PR merged from branch: {head_branch}")
                all_states = state.list_all_states()
                for s in all_states:
                    proposal = s.get("proposal")
                    if proposal and proposal.get("suggested_branch_name") == head_branch:
                        state.update_status(s["job_id"], "fixed")
                        print(f"Updated job {s['job_id']} status to fixed")
                        
                        # Delete the branch locally
                        success, msg = git_ops.delete_local_branch(TARGET_REPO_PATH, head_branch, base_branch=DEFAULT_BRANCH)
                        if success:
                            print(f"Successfully deleted local branch: {head_branch}")
                        else:
                            print(f"Failed to delete local branch: {msg}")

                        return {"status": "success", "message": f"Job {s['job_id']} marked as fixed and branch deleted locally."}
                return {"status": "ignored", "message": f"No job found for branch {head_branch}"}
        return {"status": "ignored", "reason": "PR not closed/merged"}

    # --- Handle Workflow Run Events ---
    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion")

    #print(f"[WEBHOOK] workflow_run action={action} conclusion={conclusion} run_id={workflow_run.get('id', '?')}")

    # 4. Detect whether the workflow run conclusion is failure
    if conclusion == "failure":
        background_tasks.add_task(process_workflow_run_background, payload)
        return {"status": "accepted", "message": "Workflow run processing in background"}

    return {"status": "ignored", "reason": "Workflow did not fail or is not completed"}

async def process_workflow_run_background(payload: dict):
    workflow_run = payload.get("workflow_run", {})
    repo_full_name = payload["repository"]["full_name"]
    run_id = workflow_run["id"]
    
    print(f"Detected failed workflow run: {run_id} in repo {repo_full_name}")

    # 5. Use GitHub Actions API to find jobs
    failed_job = await github_client.get_failed_job_for_run(repo_full_name, run_id)
    
    if failed_job:
        job_id = failed_job["id"]
        job_name = failed_job["name"]
        print(f"Identified failed job: {job_name} (ID: {job_id})")

        # Check for deduplication: if we already have a state for this job, ignore it.
        existing_state = state.load_state(str(job_id))
        if existing_state is not None:
            print(f"Job {job_id} already has an existing state ({existing_state.get('status')}). Skipping deduplicated webhook.")
            return

        # 6. Download the raw log for that failed job
        log_content = await github_client.download_job_log(repo_full_name, job_id)
        
        if log_content:
            # 7. Save the raw log locally
            log_file_path = f"logs/job-{job_id}.log"
            with open(log_file_path, "w", encoding="utf-8") as f:
                f.write(log_content)
            print(f"Successfully saved log to {log_file_path}")

            # 8. Parse the log
            parser_output = log_parser.parse(log_content)
            
            # Create initial state
            state.create_initial_state(str(job_id), repo_full_name, parser_output)
            state.update_status(str(job_id), "proposal_ready")
            
            # Get repo files
            repo_files = []
            if os.path.exists(TARGET_REPO_PATH):
                try:
                    # Attempt to use git ls-files to perfectly respect .gitignore
                    import subprocess
                    git_res = subprocess.run(
                        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                        cwd=TARGET_REPO_PATH,
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    repo_files = [line.strip() for line in git_res.stdout.splitlines() if line.strip()]
                except Exception:
                    # Fallback to os.walk if not a git repo or git fails
                    for root, dirs, filenames in os.walk(TARGET_REPO_PATH):
                        # Filter out directories safely
                        for d in [".git", "__pycache__", "venv", "node_modules", "dist", ".next", "build", "coverage"]:
                            if d in dirs:
                                dirs.remove(d)
                        
                        for f in filenames:
                            full_path = os.path.join(root, f)
                            rel_path = os.path.relpath(full_path, TARGET_REPO_PATH)
                            repo_files.append(rel_path)
            
            # 9. Generate AI proposal in a separate thread so it doesn't block the async loop during backoffs
            ai_res = await asyncio.to_thread(
                ai_agent.generate_proposal, 
                parser_output, 
                repo_full_name, 
                GEMINI_API_KEY, 
                repo_files
            )
            
            if ai_res["success"]:
                proposal = ai_res["proposal"]
                # 10. Validate proposal schema
                is_valid, err_msg = validators.validate_proposal_schema(proposal)
                if is_valid:
                    state.update_status(
                        str(job_id), 
                        "pending_approval", 
                        proposal=proposal,
                        raw_proposal_response=ai_res["raw_response"]
                    )
                    print(f"Proposal generated and pending approval for job {job_id}")
                else:
                    state.update_status(
                        str(job_id), 
                        "failed", 
                        error_message=f"Proposal validation failed: {err_msg}",
                        raw_proposal_response=ai_res["raw_response"]
                    )
                    print(f"Proposal validation failed: {err_msg}")
            else:
                    state.update_status(
                        str(job_id), 
                        "failed", 
                        error_message=f"AI proposal failed: {ai_res['error']}",
                        raw_proposal_response=ai_res["raw_response"]
                    )
                    print(f"AI proposal failed: {ai_res['error']}")


# --- UI and Workflow Routes ---

@app.get("/proposals", response_class=HTMLResponse)
async def list_proposals(request: Request):
    """Dashboard showing all proposals."""
    proposals = state.list_all_states()
    return templates.TemplateResponse(request=request, name="status.html", context={"request": request, "proposals": proposals})


@app.get("/proposals/{job_id}", response_class=HTMLResponse)
async def view_proposal(request: Request, job_id: str):
    """Detail page for a single proposal."""
    proposal_state = state.load_state(job_id)
    if not proposal_state:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return templates.TemplateResponse(request=request, name="proposal.html", context={"request": request, "state": proposal_state})


@app.post("/proposals/{job_id}/reject")
async def reject_proposal(job_id: str):
    """Reject a proposal."""
    proposal_state = state.load_state(job_id)
    if not proposal_state or proposal_state["status"] != "pending_approval":
        raise HTTPException(status_code=400, detail="Proposal not found or not pending approval")
    
    state.update_status(job_id, "rejected")
    return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)


@app.post("/proposals/{job_id}/retry")
async def retry_proposal(job_id: str):
    """Rerun the proposal generation for a failed job."""
    proposal_state = state.load_state(job_id)
    if not proposal_state or proposal_state["status"] != "failed":
        raise HTTPException(status_code=400, detail="Proposal not found or not in failed state")

    # Clear old proposal data and reset to parsed
    state.update_status(job_id, "parsed", proposal=None, error_message=None)
    
    repo_full_name = proposal_state["repo_full_name"]
    parser_output = proposal_state["parser_output"]

    # Get repo files
    repo_files = []
    if os.path.exists(TARGET_REPO_PATH):
        try:
            import subprocess
            git_res = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=TARGET_REPO_PATH,
                capture_output=True,
                text=True,
                check=True
            )
            repo_files = [line.strip() for line in git_res.stdout.splitlines() if line.strip()]
        except Exception:
            for root, dirs, filenames in os.walk(TARGET_REPO_PATH):
                for d in [".git", "__pycache__", "venv", "node_modules", "dist", ".next", "build", "coverage"]:
                    if d in dirs:
                        dirs.remove(d)
                for f in filenames:
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, TARGET_REPO_PATH)
                    repo_files.append(rel_path)

    # Re-run AI proposal
    ai_res = await asyncio.to_thread(
        ai_agent.generate_proposal, 
        parser_output, 
        repo_full_name, 
        GEMINI_API_KEY, 
        repo_files
    )
    
    if ai_res["success"]:
        proposal = ai_res["proposal"]
        is_valid, err_msg = validators.validate_proposal_schema(proposal)
        if is_valid:
            state.update_status(
                job_id, 
                "pending_approval", 
                proposal=proposal,
                raw_proposal_response=ai_res["raw_response"]
            )
        else:
            state.update_status(
                job_id, 
                "failed", 
                error_message=f"Proposal validation failed: {err_msg}",
                raw_proposal_response=ai_res["raw_response"]
            )
    else:
        state.update_status(
            job_id, 
            "failed", 
            error_message=f"AI proposal failed: {ai_res['error']}",
            raw_proposal_response=ai_res["raw_response"]
        )

    return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)


@app.post("/proposals/{job_id}/approve")
async def approve_proposal(job_id: str):
    """Approve a proposal and execute the fix."""
    proposal_state = state.load_state(job_id)
    if not proposal_state or proposal_state["status"] != "pending_approval":
        raise HTTPException(status_code=400, detail="Proposal not found or not pending approval")

    # Update state to approved
    proposal_state = state.update_status(job_id, "approved")
    proposal = proposal_state["proposal"]
    
    if not proposal.get("is_patchable", True):
        state.update_status(job_id, "failed", error_message="Proposal is marked as not patchable via code.")
        raise HTTPException(status_code=400, detail="Cannot approve: issue is not fixable by editing code")
    
    # 1. Pre-check: Target repo must be clean
    is_clean, err_msg = validators.validate_repo_clean(TARGET_REPO_PATH)
    if not is_clean:
        state.update_status(job_id, "failed", error_message=err_msg)
        return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)
        
    # 2. Pre-check: Validate branch name
    branch_name = proposal.get("suggested_branch_name", f"fix-job-{job_id}")
    is_valid_branch, err_msg = validators.validate_branch_name(branch_name)
    if not is_valid_branch:
        state.update_status(job_id, "failed", error_message=err_msg)
        return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)

    # 3. Create branch FIRST
    state.update_status(job_id, "executing_fix")
    success, actual_branch_name, msg = git_ops.create_branch(TARGET_REPO_PATH, branch_name, base_branch=DEFAULT_BRANCH)
    if not success:
        state.update_status(job_id, "failed", error_message=msg)
        return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)

    # 4. Read approved candidate files from the repo
    approved_candidates = proposal.get("candidate_files", [])
    approved_files_with_content = {}
    for rel_path in approved_candidates:
        full_path = os.path.join(TARGET_REPO_PATH, rel_path)
        if os.path.exists(full_path) and os.path.isfile(full_path):
            with open(full_path, "r", encoding="utf-8") as f:
                approved_files_with_content[rel_path] = f.read()
        else:
            # Maybe the file doesn't exist yet, we only pass what we have
            pass
            
    # 5. Ask AI for the fix
    ai_res = await asyncio.to_thread(
        ai_agent.generate_fix,
        proposal,
        approved_files_with_content,
        proposal_state["parser_output"],
        GEMINI_API_KEY
    )
    
    if not ai_res["success"]:
        state.update_status(job_id, "failed", error_message=ai_res["error"], raw_execution_response=ai_res["raw_response"])
        git_ops.cleanup(TARGET_REPO_PATH, base_branch=DEFAULT_BRANCH)
        return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)

    execution_result = ai_res["execution_result"]
    state.update_status(job_id, "executing_fix", raw_execution_response=ai_res["raw_response"])

    # 6. Validate Execution Result
    validations = [
        validators.validate_execution_schema(execution_result),
        validators.validate_edit_scope(execution_result, approved_candidates),
        validators.validate_file_paths([f["path"] for f in execution_result.get("files_changed", [])], TARGET_REPO_PATH),
        validators.validate_file_content(execution_result.get("files_changed", [])),
        validators.validate_yaml_files(execution_result.get("files_changed", []))
    ]
    
    for is_valid, err_msg in validations:
        if not is_valid:
            state.update_status(job_id, "failed", error_message=f"Execution validation failed: {err_msg}")
            git_ops.cleanup(TARGET_REPO_PATH, base_branch=DEFAULT_BRANCH)
            return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)
            
    # 7. Apply File Changes
    files_changed = execution_result.get("files_changed", [])
    success, msg = git_ops.apply_file_changes(TARGET_REPO_PATH, files_changed)
    if not success:
        state.update_status(job_id, "failed", error_message=msg)
        git_ops.cleanup(TARGET_REPO_PATH, base_branch=DEFAULT_BRANCH)
        return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)

    # 8. Generate Diff for Review
    diff_stat, full_diff = git_ops.generate_diff(TARGET_REPO_PATH)
    
    # 9. Stage validated files, commit, and push
    paths_to_stage = [f["path"] for f in files_changed]
    commit_message = proposal.get("suggested_pr_title", "Fix pipeline failure")
    
    success, msg = git_ops.commit_and_push(TARGET_REPO_PATH, actual_branch_name, commit_message, paths_to_stage)
    if not success:
        state.update_status(job_id, "failed", error_message=msg)
        git_ops.cleanup(TARGET_REPO_PATH, base_branch=DEFAULT_BRANCH)
        return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)
        
    state.update_status(job_id, "committed", diff_summary=diff_stat)

    # 10. Create Pull Request
    pr_title = proposal.get("suggested_pr_title", f"Fix for job {job_id}")
    pr_body = f"## 🔍 Error Analysis\n\n{proposal.get('error_explanation', '')}\n\n"
    pr_body += f"## 💡 Suggested Fix\n\n{proposal.get('suggested_fix', '')}\n\n"
    pr_body += f"## 📝 What Was Changed\n\n{execution_result.get('change_summary', '')}\n\n"
    pr_body += f"### Files Modified\n"
    for f in files_changed:
        pr_body += f"- {f.get('path')} ({f.get('action')})\n"
    pr_body += f"\n## 📊 Diff Summary\n\n```\n{diff_stat}\n```\n\n---\n*Generated by AI Pipeline Fixer*"

    # We need to run create_pull_request using await since it's an async method
    pr_res = await github_client.create_pull_request(
        repo_full_name=proposal_state["repo_full_name"],
        head_branch=actual_branch_name,
        base_branch=DEFAULT_BRANCH,
        title=pr_title,
        body=pr_body
    )
    
    if pr_res and "html_url" in pr_res:
        state.update_status(job_id, "pr_created", pr_url=pr_res["html_url"], execution_result=execution_result)
    else:
        state.update_status(job_id, "failed", error_message="PR creation failed", execution_result=execution_result)
        
    # Cleanup checkout back to base branch
    git_ops.cleanup(TARGET_REPO_PATH, base_branch=DEFAULT_BRANCH)

    return RedirectResponse(url=f"/proposals/{job_id}", status_code=303)
