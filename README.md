# 🔧 AI Pipeline Fixer

An intelligent, human-in-the-loop agent that automatically detects failed GitHub Actions workflows, explains the error, proposes a code fix, applies it on a new branch, and opens a Pull Request — all with your explicit approval before a single file is touched.

---

## ✨ How It Works

```
GitHub Action Fails
        │
        ▼
  Webhook received
  (workflow_run → completed → failure)
        │
        ▼
  Download failed job log
  Save to logs/
        │
        ▼
  Parse log (ANSI strip, pattern match)
  Produce structured JSON (error_label, snippet)
        │
        ▼
  Send to Gemini AI → Proposal
  (explanation, fix, candidate files, risk level, branch name)
        │
        ▼
  Show in Dashboard
  ┌─────────────┐
  │  ✅ Approve │  ←── Human decision point
  │  ❌ Reject  │
  └─────────────┘
        │ Approved
        ▼
  Pre-flight checks:
  • Repo is clean (no uncommitted changes)
  • Branch name is safe
        │
        ▼
  Create new branch
        │
        ▼
  Send only approved files to Gemini → Execution
  (returns exact new file contents)
        │
        ▼
  Validate AI output:
  • JSON schema valid
  • Only approved files changed
  • File paths are safe (no traversal, no .git)
  • File content is not empty or too large
  • YAML files still parse correctly
        │
        ▼
  Write files to disk
  Generate diff (git diff)
  Stage only changed files
  Commit + Push to new branch
        │
        ▼
  Open PR on GitHub
  (with AI explanation + change summary in body)
        │
        ▼
  PR merged on GitHub
  Webhook → pull_request → closed + merged
        │
        ▼
  Job status updated to ✅ fixed
```

---

## 🗂️ Project Structure

```
ai-pipeline-fixer/
├── main.py                  # FastAPI app — webhook, routes, approval flow
├── requirements.txt
├── .env                     # Environment variables (not committed)
├── .gitignore
│
├── core/                    # All business logic
│   ├── __init__.py
│   ├── ai_agent.py          # Gemini API calls (proposal + execution)
│   ├── git_ops.py           # Git branch, commit, push, diff, cleanup
│   ├── github_client.py     # GitHub REST API (jobs, logs, PRs)
│   ├── log_parser.py        # ANSI stripping, error pattern matching
│   ├── state.py             # JSON-file-based state persistence
│   └── validators.py        # AI output validation, path safety, YAML checks
│
├── templates/
│   ├── status.html          # Dashboard — lists all proposals
│   └── proposal.html        # Detail page — view, approve, reject a proposal
│
├── logs/                    # Raw downloaded runner logs (gitignored)
└── proposals/               # Persisted JSON state per job (gitignored)
```

---

## ⚙️ Setup

### Prerequisites

- Python 3.11+
- A GitHub repository to monitor (the "demo repo")
- A GitHub Personal Access Token (PAT) with `repo` and `workflow` scopes
- A Google Gemini API key from [aistudio.google.com](https://aistudio.google.com/apikey)
- [ngrok](https://ngrok.com/) (for local webhook development)

### 1. Clone and install

```bash
git clone https://github.com/cmbigk/ai-pipeline-fixer
cd ai-pipeline-fixer
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root:

```env
# GitHub Personal Access Token (with repo + workflow scopes)
GITHUB_TOKEN=ghp_your_token_here

# Webhook secret (match this in your GitHub webhook settings)
GITHUB_WEBHOOK_SECRET=your_secret_word

# Google Gemini API key
GEMINI_API_KEY=your_gemini_key_here

# Absolute local path to the repository where AI will apply fixes
TARGET_REPO_PATH=/Users/you/Desktop/ai-pipeline-fixer-demo
```

### 3. Start the server

```bash
python -m uvicorn main:app --reload
```

The dashboard will be available at `http://localhost:8000/proposals`.

### 4. Expose your local server

```bash
ngrok http 8000
```

Copy the `https://xxxx.ngrok-free.app` URL. You will use it as your webhook URL.

### 5. Configure the GitHub Webhook

In your target repository on GitHub:
- Go to **Settings → Webhooks → Add webhook**
- **Payload URL**: `https://xxxx.ngrok-free.app/webhook`
- **Content type**: `application/json`
- **Secret**: Same value as `GITHUB_WEBHOOK_SECRET` in your `.env`
- **Events**: Select **"Let me select individual events"** and check:
  - ✅ **Workflow runs**
  - ✅ **Pull requests**
- Click **Add webhook**

---

## 🖥️ Dashboard & UI

### Dashboard (`/proposals`)

Lists all proposals with their current status. Statuses include:

| Status | Meaning |
|---|---|
| `proposal_ready` | Log parsed, AI proposal being generated |
| `pending_approval` | Waiting for human approval |
| `approved` | User approved, execution starting |
| `executing_fix` | AI generating and applying code fix |
| `pr_created` | Branch pushed, PR opened on GitHub |
| `fixed` | PR was merged — issue resolved! |
| `rejected` | User rejected the proposal |
| `failed` | An error occurred at some stage |

### Proposal Detail (`/proposals/{job_id}`)

Shows:
- **Parsed Error**: error label, primary error line, full failure snippet
- **AI Proposal**: explanation, suggested fix, patchability, risk level, candidate files, branch name, PR title
- **Diff Summary**: exact code changes made (after execution)
- **PR Link**: clickable link with a "Copy Link" button
- **Approve / Reject** buttons (only when `pending_approval`)
- **🔁 Rerun Proposal** button (only when `failed`)

---

## 🔐 Safety & Guardrails

The system enforces multiple layers of protection before any file is written:

| Check | Where |
|---|---|
| Webhook HMAC-SHA256 signature verified | `main.py` |
| Proposal JSON schema validated | `validators.validate_proposal_schema` |
| Execution JSON schema validated | `validators.validate_execution_schema` |
| `files_changed` must not be empty | `validators.validate_execution_schema` |
| AI can only edit approved candidate files | `validators.validate_edit_scope` |
| File paths must be relative (no absolute) | `validators.validate_file_paths` |
| Directory traversal (`../`) rejected | `validators.validate_file_paths` |
| `.git/` directory edits rejected | `validators.validate_file_paths` |
| Paths verified to stay inside repo root | `validators.validate_file_paths` |
| Files must not be empty or exceed 500 lines | `validators.validate_file_content` |
| YAML files re-parsed after edit | `validators.validate_yaml_files` |
| Target repo must be clean before branching | `validators.validate_repo_clean` |
| Branch name character-safe | `validators.validate_branch_name` |
| Only staged files are committed (not `git add .`) | `git_ops.commit_and_push` |
| Gemini 429 rate limits retried with backoff | `ai_agent.generate_proposal/generate_fix` |

---

## 🧪 Error Handling

- If AI returns invalid JSON → retried up to 5 times, then failed with clear message
- If AI hits rate limit (429) → sleeps 15s between retries (up to 75s total)
- If branch already exists → auto-retried with `-retry` suffix
- If any validation fails → execution stops, repo cleaned up (checked out back to `main`), error shown in UI
- If PR creation fails → error stored in state, shown in dashboard

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `fastapi[standard]` | Web server and routing |
| `httpx` | Async HTTP calls to GitHub and Gemini |
| `google-genai` | Gemini AI SDK |
| `python-dotenv` | `.env` file loading |
| `pyyaml` | YAML syntax validation |
| `jinja2` | HTML template rendering |

---

## 🔄 State Persistence

Each job's state is written to `proposals/{job_id}.json` immediately after every status change. This means:
- The server can be restarted at any time without losing proposal data.
- All historical proposals remain visible in the dashboard.
- State files contain: job ID, status, parsed output, full proposal, execution result, diff, PR URL, error messages, and timestamps.

---
