import os
import hmac
import hashlib
import json
from fastapi import FastAPI, Request, HTTPException, Header
from dotenv import load_dotenv

from github_client import GitHubClient

# Load environment variables
load_dotenv()

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

app = FastAPI(title="AI Pipeline Fixer Webhook")

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

github_client = GitHubClient(token=GITHUB_TOKEN)

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
    if x_github_event != "workflow_run":
        return {"status": "ignored", "reason": f"Not a workflow_run event. Got: {x_github_event}"}

    payload = json.loads(payload_body)
    action = payload.get("action")
    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion")

    # 4. Detect whether the workflow run conclusion is failure
    if action == "completed" and conclusion == "failure":
        repo_full_name = payload["repository"]["full_name"]
        run_id = workflow_run["id"]
        
        print(f"Detected failed workflow run: {run_id} in repo {repo_full_name}")

        # 5. Use GitHub Actions API to find jobs
        failed_job = await github_client.get_failed_job_for_run(repo_full_name, run_id)
        
        if failed_job:
            job_id = failed_job["id"]
            job_name = failed_job["name"]
            print(f"Identified failed job: {job_name} (ID: {job_id})")

            # 6. Download the raw log for that failed job
            log_content = await github_client.download_job_log(repo_full_name, job_id)
            
            if log_content:
                # 7. Save the raw log locally
                log_file_path = f"logs/job-{job_id}.log"
                with open(log_file_path, "w", encoding="utf-8") as f:
                    f.write(log_content)
                print(f"Successfully saved log to {log_file_path}")
                return {"status": "success", "message": f"Log saved to {log_file_path}"}
            else:
                print("Failed to download log content.")
                return {"status": "error", "message": "Could not download log"}
        else:
            print(f"No failed job found for run {run_id}")
            return {"status": "error", "message": "Failed job not found"}

    return {"status": "ignored", "reason": "Workflow did not fail or is not completed"}
