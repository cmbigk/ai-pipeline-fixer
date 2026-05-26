import httpx
from typing import Optional

class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.base_url = "https://api.github.com"

    async def get_failed_job_for_run(self, repo_full_name: str, run_id: int) -> Optional[dict]:
        """
        Fetches all jobs for a workflow run and returns the failed job with the longest log.
        """
        url = f"{self.base_url}/repos/{repo_full_name}/actions/runs/{run_id}/jobs"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            failed_jobs = [job for job in data.get("jobs", []) if job.get("conclusion") == "failure"]
            
            if not failed_jobs:
                return None
                
            if len(failed_jobs) == 1:
                return failed_jobs[0]
                
            # If multiple failed jobs, fetch their logs to find the most informative one (longest)
            best_job = failed_jobs[0]
            max_len = -1
            
            for job in failed_jobs:
                log = await self.download_job_log(repo_full_name, job["id"])
                if log and len(log) > max_len:
                    max_len = len(log)
                    best_job = job
                    
            return best_job

    async def download_job_log(self, repo_full_name: str, job_id: int) -> Optional[str]:
        """
        Downloads the raw log for a specific job.
        Returns the log text if successful.
        """
        url = f"{self.base_url}/repos/{repo_full_name}/actions/jobs/{job_id}/logs"
        async with httpx.AsyncClient() as client:
            # GitHub API redirects to the actual log URL, httpx handles this by default with follow_redirects=True
            response = await client.get(url, headers=self.headers, follow_redirects=True)
            if response.status_code == 200:
                return response.text
            else:
                print(f"Failed to download log for job {job_id}: {response.status_code} {response.text}")
                return None

    async def create_pull_request(
        self,
        repo_full_name: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> Optional[dict]:
        """
        Create a pull request via the GitHub API.
        Returns the PR data dict if successful.
        """
        url = f"{self.base_url}/repos/{repo_full_name}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=payload)
            if response.status_code == 201:
                return response.json()
            else:
                print(f"Failed to create PR: {response.status_code} {response.text}")
                return None

