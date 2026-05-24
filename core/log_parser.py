import re
import json
from typing import List, Dict, Any

class LogParser:
    def __init__(self):
        # Regex to match ANSI escape codes (colors, formatting)
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        # Patterns that strongly indicate a failure, mapped to labels
        self.error_patterns = [
            (r'command not found', 'command_not_found'),
            (r'no such file', 'file_not_found'),
            (r'permission denied', 'permission_denied'),
            (r'not set', 'missing_env_var'),
            (r'fail(ed|ure)?', 'test_failure'),
            (r'error:', 'unknown_failure'),
            (r'exception', 'unknown_failure'),
            (r'exit code \d+', 'unknown_failure')
        ]

    def clean_log(self, raw_log: str) -> List[str]:
        """Removes ANSI codes and empty lines to prepare for analysis."""
        cleaned_lines = []
        for line in raw_log.splitlines():
            # Remove ANSI colors
            clean_line = self.ansi_escape.sub('', line).strip()
            # Ignore empty lines
            if clean_line:
                # Optionally, you could strip standard GitHub timestamps here
                # e.g., clean_line = re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s+', '', clean_line)
                cleaned_lines.append(clean_line)
        return cleaned_lines

    def parse(self, raw_log: str) -> Dict[str, Any]:
        """Parses the raw log and extracts the failure snippet and label."""
        lines = self.clean_log(raw_log)
        
        primary_error_line = ""
        error_label = "unknown_failure"
        matched_patterns = []
        error_index = -1
        
        # Scan lines from top to bottom to find the first/strongest error indicator
        for i, line in enumerate(lines):
            line_lower = line.lower()
            
            for pattern, label in self.error_patterns:
                if re.search(pattern, line_lower):
                    if error_index == -1:
                        # Capture the first matched error as the primary issue
                        primary_error_line = line
                        error_label = label
                        error_index = i
                    if pattern not in matched_patterns:
                        matched_patterns.append(pattern)
        
        # If no error found, return a default safe response
        if error_index == -1:
            return {
                "error_label": "no_error_detected",
                "primary_error_line": "",
                "failure_snippet": "",
                "matched_patterns": [],
                "confidence": 0.0
            }
            
        # Extract surrounding context (e.g., 3 lines before and 5 lines after)
        start_idx = max(0, error_index - 5)
        end_idx = min(len(lines), error_index + 10)
        failure_snippet = "\n".join(lines[start_idx:end_idx])
        
        # Determine a basic confidence score
        confidence = 0.85 if len(matched_patterns) > 0 else 0.4
        
        return {
            "error_label": error_label,
            "primary_error_line": primary_error_line,
            "failure_snippet": failure_snippet,
            "matched_patterns": matched_patterns,
            "confidence": confidence
        }

if __name__ == "__main__":
    import glob
    import os

    parser = LogParser()

    # Find all log files in the logs/ directory
    log_files = glob.glob("logs/job-*.log")

    if log_files:
        # Get the newest log file based on modification time
        newest_log_file = max(log_files, key=os.path.getmtime)
        print(f"--- Parsing the latest log file: {newest_log_file} ---")
        with open(newest_log_file, "r", encoding="utf-8") as f:
            log_content = f.read()
        result = parser.parse(log_content)
        print(json.dumps(result, indent=2))
    else:
        # Fallback to sample log if logs/ is empty
        print("--- No log files found in logs/. Using fallback sample log. ---")
        

