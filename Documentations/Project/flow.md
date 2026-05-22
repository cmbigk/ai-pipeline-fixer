Here is the full MVP flow:
A GitLab pipeline fails in your demo repo.
Your backend detects the failure by polling GitLab or by receiving a webhook.
Backend fetches the failed pipeline’s jobs, finds the failed job, and downloads the raw job trace.
Parser reduces the log to the meaningful error window, removes noise, and masks secrets before sending anything to AI.
A lightweight classifier labels the failure type, for example Terraform credentials, missing env var, Docker build error, dependency install failure, or test failure.
Local model generates:
explanation in plain English,
likely root cause,
suggested fix,
confidence score.
Backend stores that result in a database so the UI can show it and you can keep an audit trail.
Engineer opens your UI, reviews the suggestion, and clicks Approve.
Backend generates or edits the target file in a new branch.
Backend pushes that branch and creates a GitLab merge request through the API.
Engineer reviews the MR like normal.
After merge, the normal GitLab pipeline runs again.
Optional later feature: backend can trigger retry/rerun after approval.
That is a complete, believable DevOps product loop.