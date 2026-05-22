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
        Fetches all jobs for a workflow run and returns the first failed job.
        """
        url = f"{self.base_url}/repos/{repo_full_name}/actions/runs/{run_id}/jobs"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            for job in data.get("jobs", []):
                if job.get("conclusion") == "failure":
                    return job
        return None

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
