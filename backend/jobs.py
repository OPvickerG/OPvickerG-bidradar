"""Manual 'Scan now' trigger.

With GitHub Actions hosting (zero-cost setup) the dashboard dispatches the scan workflow using a fine-grained
token limited to that repository's Actions. In Cloud Run the dashboard starts an execution of the scanner Cloud Run Job through the Run Admin API, using
the service's own identity from the metadata server (no keys stored anywhere). Locally it runs the scan in a
background thread. Either way the scan lock prevents overlap with the scheduled run."""
from __future__ import annotations

import os
import threading
from typing import Tuple

import httpx

_META = "http://metadata.google.internal/computeMetadata/v1"


def _cloud_target() -> Tuple[str, str, str]:
    return (os.environ.get("GCP_PROJECT", ""), os.environ.get("GCP_REGION", ""),
            os.environ.get("SCAN_JOB_NAME", ""))


def trigger_manual_scan() -> str:
    repo, token = os.environ.get("GITHUB_REPO", ""), os.environ.get("GITHUB_DISPATCH_TOKEN", "")
    if repo and token:
        r = httpx.post(f"https://api.github.com/repos/{repo}/actions/workflows/scan.yml/dispatches",
                       headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                "X-GitHub-Api-Version": "2022-11-28"},
                       json={"ref": os.environ.get("GITHUB_REF_NAME", "main"), "inputs": {"trigger": "manual"}},
                       timeout=20)
        r.raise_for_status()
        return "Started the scan workflow on GitHub Actions. It usually begins within a minute; refresh in a few minutes."
    project, region, job = _cloud_target()
    if project and region and job:
        tok = httpx.get(f"{_META}/instance/service-accounts/default/token",
                        headers={"Metadata-Flavor": "Google"}, timeout=5).json()["access_token"]
        r = httpx.post(f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run",
                       headers={"Authorization": f"Bearer {tok}"},
                       json={"overrides": {"containerOverrides": [{"env": [{"name": "SCAN_TRIGGER", "value": "manual"}]}]}},
                       timeout=20)
        r.raise_for_status()
        return "Started a scanner job execution in Cloud Run. Refresh the Monitoring Centre in a few minutes."
    from scanner.scheduler.run import run_once
    threading.Thread(target=run_once, kwargs={"trigger": "manual"}, daemon=True, name="manual-scan").start()
    return "Started a local manual scan in the background. Refresh this page in a few minutes."
