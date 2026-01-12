#!/usr/bin/env python3
"""
Poll GitHub Actions workflow runs and display logs.

Usage:
    python poll_github_actions.py [--repo OWNER/REPO] [--run-id ID] [--poll-interval SECONDS]

Examples:
    # Poll latest run for this repo
    python poll_github_actions.py

    # Poll specific run
    python poll_github_actions.py --run-id 12345678

    # Custom poll interval (default 30 seconds)
    python poll_github_actions.py --poll-interval 15

Environment Variables:
    GITHUB_TOKEN - Required for API authentication. Create a token at:
                   https://github.com/settings/tokens with 'repo' scope
"""

import os
import sys
import time
import argparse
import zipfile
import io
from datetime import datetime

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Install with: pip install requests")
    sys.exit(1)

# Default repo - update this to match your repo
DEFAULT_REPO = "ktronicsdev/IoTDeviceMonitor"
GITHUB_API_BASE = "https://api.github.com"


def get_github_token():
    """Get GitHub token from environment."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN environment variable not set.")
        print("Create a token at: https://github.com/settings/tokens")
        print("Then set it: set GITHUB_TOKEN=your_token_here (Windows)")
        print("        or: export GITHUB_TOKEN=your_token_here (Linux/Mac)")
        sys.exit(1)
    return token


def github_api_request(endpoint, token, method="GET"):
    """Make a request to GitHub API."""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "poll-github-actions-py"
    }
    url = f"{GITHUB_API_BASE}{endpoint}"

    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=60)
        else:
            response = requests.request(method, url, headers=headers, timeout=60)

        if response.status_code == 401:
            print("ERROR: Invalid GitHub token. Please check your GITHUB_TOKEN.")
            sys.exit(1)
        elif response.status_code == 404:
            print(f"ERROR: Resource not found: {endpoint}")
            return None
        elif response.status_code != 200:
            print(f"ERROR: API request failed with status {response.status_code}")
            print(f"Response: {response.text}")
            return None

        return response.json()
    except requests.exceptions.Timeout:
        print("ERROR: Request timed out")
        return None
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Request failed: {e}")
        return None


def get_latest_run(repo, token):
    """Get the latest workflow run."""
    endpoint = f"/repos/{repo}/actions/runs?per_page=1"
    data = github_api_request(endpoint, token)

    if not data or "workflow_runs" not in data:
        return None

    runs = data["workflow_runs"]
    if not runs:
        return None

    run = runs[0]
    return {
        "databaseId": run["id"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "name": run["name"],
        "headBranch": run["head_branch"],
        "createdAt": run["created_at"],
        "updatedAt": run["updated_at"]
    }


def get_run_details(repo, run_id, token):
    """Get details for a specific run including jobs."""
    # Get run info
    run_endpoint = f"/repos/{repo}/actions/runs/{run_id}"
    run_data = github_api_request(run_endpoint, token)

    if not run_data:
        return None

    # Get jobs for this run
    jobs_endpoint = f"/repos/{repo}/actions/runs/{run_id}/jobs"
    jobs_data = github_api_request(jobs_endpoint, token)

    jobs = []
    if jobs_data and "jobs" in jobs_data:
        for job in jobs_data["jobs"]:
            job_info = {
                "id": job["id"],
                "name": job["name"],
                "status": job["status"],
                "conclusion": job["conclusion"],
                "steps": []
            }
            for step in job.get("steps", []):
                job_info["steps"].append({
                    "name": step["name"],
                    "status": step["status"],
                    "conclusion": step["conclusion"]
                })
            jobs.append(job_info)

    return {
        "databaseId": run_data["id"],
        "status": run_data["status"],
        "conclusion": run_data["conclusion"],
        "name": run_data["name"],
        "createdAt": run_data["created_at"],
        "updatedAt": run_data["updated_at"],
        "jobs": jobs
    }


def get_run_logs(repo, run_id, token):
    """Get logs for a workflow run."""
    # GitHub API returns logs as a zip file
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "poll-github-actions-py"
    }
    url = f"{GITHUB_API_BASE}/repos/{repo}/actions/runs/{run_id}/logs"

    try:
        response = requests.get(url, headers=headers, timeout=120, allow_redirects=True)

        if response.status_code == 404:
            return "Logs not available (run may still be in progress or logs expired)"

        if response.status_code != 200:
            return f"Error getting logs: HTTP {response.status_code}"

        # Logs are returned as a zip file
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                all_logs = []
                for name in sorted(z.namelist()):
                    if name.endswith('.txt'):
                        all_logs.append(f"\n{'='*60}\n📄 {name}\n{'='*60}")
                        content = z.read(name).decode('utf-8', errors='replace')
                        all_logs.append(content)
                return "\n".join(all_logs) if all_logs else "No log files found in archive"
        except zipfile.BadZipFile:
            return "Error: Invalid log archive received"

    except requests.exceptions.Timeout:
        return "Error: Request timed out while fetching logs"
    except requests.exceptions.RequestException as e:
        return f"Error fetching logs: {e}"


def format_status(status, conclusion):
    """Format status with emoji."""
    if status == "in_progress":
        return "🔄 In Progress"
    elif status == "queued":
        return "⏳ Queued"
    elif status == "completed":
        if conclusion == "success":
            return "✅ Success"
        elif conclusion == "failure":
            return "❌ Failed"
        elif conclusion == "cancelled":
            return "⚪ Cancelled"
        else:
            return f"⚠️ {conclusion}"
    return f"❓ {status}"


def display_run_summary(run_details):
    """Display a summary of the workflow run."""
    print("\n" + "=" * 80)
    print(f"Workflow: {run_details.get('name', 'Unknown')}")
    print(f"Run ID:   {run_details.get('databaseId', 'Unknown')}")
    print(f"Status:   {format_status(run_details.get('status', ''), run_details.get('conclusion', ''))}")
    print(f"Created:  {run_details.get('createdAt', 'Unknown')}")
    print(f"Updated:  {run_details.get('updatedAt', 'Unknown')}")
    print("=" * 80)

    # Display jobs
    jobs = run_details.get('jobs', [])
    if jobs:
        print("\nJobs:")
        for job in jobs:
            job_status = format_status(job.get('status', ''), job.get('conclusion', ''))
            print(f"  - {job.get('name', 'Unknown')}: {job_status}")

            # Display steps
            steps = job.get('steps', [])
            for step in steps:
                step_status = format_status(step.get('status', ''), step.get('conclusion', ''))
                step_name = step.get('name', 'Unknown')
                print(f"      [{step_status}] {step_name}")
    print()


def poll_workflow(repo, run_id, poll_interval, show_logs, token):
    """Poll a workflow run until completion."""

    if run_id is None:
        print(f"Getting latest run for {repo}...")
        latest = get_latest_run(repo, token)
        if not latest:
            print("No workflow runs found.")
            return
        run_id = latest['databaseId']
        print(f"Found run #{run_id}: {latest['name']}")

    print(f"\nPolling run #{run_id} every {poll_interval} seconds...")
    print("Press Ctrl+C to stop\n")

    last_status = None

    try:
        while True:
            run_details = get_run_details(repo, run_id, token)

            if not run_details:
                print("Failed to get run details. Retrying...")
                time.sleep(poll_interval)
                continue

            current_status = (run_details.get('status'), run_details.get('conclusion'))

            # Display summary if status changed
            if current_status != last_status:
                display_run_summary(run_details)
                last_status = current_status

            # Check if completed
            if run_details.get('status') == 'completed':
                print("\n" + "=" * 80)
                print("WORKFLOW COMPLETED")
                print("=" * 80)

                if show_logs:
                    print("\n📋 FULL LOGS:")
                    print("-" * 80)
                    logs = get_run_logs(repo, run_id, token)
                    print(logs)

                # Show conclusion
                conclusion = run_details.get('conclusion', 'unknown')
                if conclusion == 'success':
                    print("\n✅ WORKFLOW SUCCEEDED")
                else:
                    print(f"\n❌ WORKFLOW FAILED: {conclusion}")

                return run_details

            # Still running - show progress
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] Still running... (next check in {poll_interval}s)")
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\nPolling stopped by user.")

        # Offer to show current logs
        response = input("Show current logs? (y/n): ")
        if response.lower() == 'y':
            logs = get_run_logs(repo, run_id, token)
            print(logs)


def main():
    parser = argparse.ArgumentParser(
        description="Poll GitHub Actions workflow runs and display logs"
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"Repository in OWNER/REPO format (default: {DEFAULT_REPO})"
    )
    parser.add_argument(
        "--run-id",
        type=int,
        help="Specific run ID to poll (default: latest run)"
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between polls (default: 30)"
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Don't show full logs when complete"
    )
    parser.add_argument(
        "--logs-only",
        action="store_true",
        help="Just fetch and display logs for the run, don't poll"
    )

    args = parser.parse_args()

    print("=" * 80)
    print("GitHub Actions Workflow Poller")
    print("=" * 80)

    # Get token
    token = get_github_token()

    if args.logs_only:
        run_id = args.run_id
        if run_id is None:
            latest = get_latest_run(args.repo, token)
            if latest:
                run_id = latest['databaseId']
            else:
                print("No runs found.")
                return

        print(f"Fetching logs for run #{run_id}...")
        logs = get_run_logs(args.repo, run_id, token)
        print(logs)
    else:
        poll_workflow(
            repo=args.repo,
            run_id=args.run_id,
            poll_interval=args.poll_interval,
            show_logs=not args.no_logs,
            token=token
        )


if __name__ == "__main__":
    main()
