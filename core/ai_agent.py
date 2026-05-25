import os
import json
from google import genai

# Gemini model — configurable. Check current pricing/limits at https://ai.google.dev/pricing
GEMINI_MODEL = "gemini-2.0-flash"

# Prompt for the proposal phase
PROPOSAL_SYSTEM_PROMPT = """You are a DevOps error analysis assistant.
You receive a structured JSON description of a failed GitHub Actions workflow.
Your job is to analyze the failure and return a structured JSON proposal.

You MUST respond with ONLY valid JSON, no markdown, no code fences, no extra text.
The JSON must follow this exact schema:

{
  "error_explanation": "string - clear explanation of what went wrong",
  "suggested_fix": "string - practical fix suggestion",
  "is_patchable": true/false,
  "candidate_files": ["list of relative file paths that need to be changed"],
  "risk_level": "low" or "medium" or "high",
  "suggested_branch_name": "fix/short-description",
  "suggested_pr_title": "Fix: short description",
  "pr_description_summary": "string - summary for the PR body"
}

Rules:
- Be specific about what failed and why.
- candidate_files must be relative paths from the repo root.
- If the issue is a missing secret/env var and cannot be fixed by editing code, set is_patchable to false.
- If the issue CAN be fixed by editing a workflow file or code, set is_patchable to true.
- Keep the branch name short and lowercase with slashes.
- Keep the PR title concise.
"""

# Prompt for the execution phase
EXECUTION_SYSTEM_PROMPT = """You are a DevOps code fix assistant.
You receive an approved fix proposal and the current content of specific files.
Your job is to apply the fix by returning the modified file contents.

You MUST respond with ONLY valid JSON, no markdown, no code fences, no extra text.
The JSON must follow this exact schema:

{
  "files_changed": [
    {
      "path": "relative/path/to/file",
      "action": "modify",
      "new_content": "the entire new file content after the fix"
    }
  ],
  "change_summary": "string - brief description of what was changed"
}

Rules:
- You may ONLY edit the files listed in the input. Do NOT create new files. Do NOT modify other files.
- Return the COMPLETE file content, not just a diff or partial snippet.
- Make the minimum change necessary to fix the issue.
- Do not touch unrelated parts of the files.
- Keep the fix narrowly scoped to the approved issue.
"""


def _extract_json_from_response(text: str) -> dict:
    """Try to parse JSON from the AI response, handling code fences if present."""
    cleaned = text.strip()

    # Strip markdown code fences if the model wrapped its response
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    return json.loads(cleaned)


def generate_proposal(parser_output: dict, repo_full_name: str, api_key: str, repo_files: list[str] = None) -> dict:
    """
    Send parsed error context to Gemini and get a structured proposal.
    Returns {"success": True, "proposal": {...}, "raw_response": "..."} or
            {"success": False, "error": "...", "raw_response": "..."}
    Retries once if the JSON is malformed.
    """
    client = genai.Client(api_key=api_key)

    repo_files_str = "\n".join(repo_files) if repo_files else "Unknown"

    user_prompt = f"""Analyze this GitHub Actions failure from repo '{repo_full_name}':

Error label: {parser_output.get('error_label', 'unknown')}
Primary error line: {parser_output.get('primary_error_line', 'N/A')}
Matched patterns: {parser_output.get('matched_patterns', [])}
Confidence: {parser_output.get('confidence', 0)}

Failure snippet:
{parser_output.get('failure_snippet', 'No snippet available')}

Files in the repository (use these exact paths in your candidate_files):
{repo_files_str}

Return your analysis as the specified JSON structure."""

    raw_response = ""
    last_error = ""
    for attempt in range(5):  # Increased to 5 attempts
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_prompt if attempt == 0 else f"Your previous response was not valid JSON. Please try again with ONLY valid JSON.\n\nOriginal request:\n{user_prompt}",
                config={
                    "system_instruction": PROPOSAL_SYSTEM_PROMPT,
                    "temperature": 0.2,
                },
            )
            raw_response = response.text
            proposal = _extract_json_from_response(raw_response)
            return {"success": True, "proposal": proposal, "raw_response": raw_response}

        except json.JSONDecodeError as e:
            last_error = f"JSONDecodeError: {str(e)}"
            print(f"Proposal attempt {attempt + 1}: Invalid JSON from Gemini, retrying...")
            continue
        except Exception as e:
            last_error = str(e)
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "Quota exceeded" in str(e):
                backoff_time = 5 * (2 ** attempt)
                print(f"Proposal attempt {attempt + 1}: Rate limit exceeded (429). Sleeping {backoff_time} seconds...")
                import time
                time.sleep(backoff_time)
                continue
                
            return {
                "success": False,
                "error": f"Gemini API error: {last_error}",
                "raw_response": raw_response,
            }

    return {"success": False, "error": f"Exceeded maximum retries. Last error: {last_error}", "raw_response": raw_response}


def generate_fix(
    proposal: dict,
    approved_files_with_content: dict,
    parsed_error_context: dict,
    api_key: str,
) -> dict:
    """
    Send approved proposal + only the approved candidate files to Gemini for execution.
    approved_files_with_content: {"path/to/file": "file content string", ...}
    Returns {"success": True, "execution_result": {...}, "raw_response": "..."} or
            {"success": False, "error": "...", "raw_response": "..."}
    """
    client = genai.Client(api_key=api_key)

    # Build file context — only approved files
    files_section = ""
    for path, content in approved_files_with_content.items():
        files_section += f"\n--- File: {path} ---\n{content}\n--- End of {path} ---\n"

    user_prompt = f"""Apply the approved fix to the following files.

## Approved Proposal
Error explanation: {proposal.get('error_explanation', '')}
Suggested fix: {proposal.get('suggested_fix', '')}

## Original Error Context
Error label: {parsed_error_context.get('error_label', '')}
Primary error line: {parsed_error_context.get('primary_error_line', '')}
Failure snippet:
{parsed_error_context.get('failure_snippet', '')}

## Files You May Edit (ONLY these files)
{files_section}

Return the modified files as the specified JSON structure."""

    raw_response = ""
    last_error = ""
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_prompt if attempt == 0 else f"Your previous response was not valid JSON. Please try again with ONLY valid JSON.\n\nOriginal request:\n{user_prompt}",
                config={
                    "system_instruction": EXECUTION_SYSTEM_PROMPT,
                    "temperature": 0.1,
                },
            )
            raw_response = response.text
            execution_result = _extract_json_from_response(raw_response)
            return {"success": True, "execution_result": execution_result, "raw_response": raw_response}

        except json.JSONDecodeError as e:
            last_error = f"JSONDecodeError: {str(e)}"
            print(f"Execution attempt {attempt + 1}: Invalid JSON from Gemini, retrying...")
            continue
        except Exception as e:
            last_error = str(e)
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "Quota exceeded" in str(e):
                backoff_time = 5 * (2 ** attempt)
                print(f"Execution attempt {attempt + 1}: Rate limit exceeded (429). Sleeping {backoff_time} seconds...")
                import time
                time.sleep(backoff_time)
                continue
                
            return {
                "success": False,
                "error": f"Gemini API error: {last_error}",
                "raw_response": raw_response,
            }

    return {"success": False, "error": f"Exceeded maximum retries. Last error: {last_error}", "raw_response": raw_response}
