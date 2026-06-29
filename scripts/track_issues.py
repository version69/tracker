#!/usr/bin/env python3
"""Hourly GitHub issue watcher with Resend email notifications."""

from __future__ import annotations

import html
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GITHUB_API_URL = "https://api.github.com"
RESEND_EMAIL_URL = "https://api.resend.com/emails"
DEFAULT_EMAIL_FROM = "Good First Issues <onboarding@resend.dev>"
REPOS_PATH = Path("repos.json")
SEEN_PATH = Path("seen.json")
DEFAULT_LABELS = [
    "good first issue",
    "good-first-issue",
    "easy",
    "easy task",
    "beginner",
    "beginner-friendly",
    "first-timers-only",
    "help wanted",
]


@dataclass(frozen=True)
class Issue:
    key: str
    title: str
    url: str
    repo: str
    number: int
    created_at: str
    labels: list[str]
    comments: int
    author: str


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default or []
    return [item.strip() for item in raw.split(",") if item.strip()]


def normalize_repo(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None

    prefix = "https://github.com/"
    if not value.startswith(prefix):
        return None

    repo = value.removeprefix(prefix).split("?", 1)[0].split("#", 1)[0].strip("/")
    repo = repo.removesuffix(".git")
    parts = repo.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None

    owner, name = parts[:2]
    return f"{owner}/{name}"


def load_repos() -> list[str]:
    repos: list[str] = []
    seen: set[str] = set()

    if REPOS_PATH.exists():
        with REPOS_PATH.open("r", encoding="utf-8") as file:
            repo_links = json.load(file)

        if not isinstance(repo_links, list):
            raise RuntimeError("repos.json must be a JSON array of GitHub repository links.")

        for link in repo_links:
            if not isinstance(link, str):
                raise RuntimeError("repos.json must contain only GitHub repository links as strings.")

            repo = normalize_repo(link)
            if not repo:
                raise RuntimeError(f"Invalid GitHub repository link in repos.json: {link}")
            if repo and repo not in seen:
                repos.append(repo)
                seen.add(repo)

    return repos


def load_seen() -> dict[str, Any]:
    if not SEEN_PATH.exists():
        return {"issues": {}, "updated_at": None}

    with SEEN_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return {"issues": {key: {"first_seen_at": None} for key in data}, "updated_at": None}

    data.setdefault("issues", {})
    data.setdefault("updated_at", None)
    return data


def save_seen(seen: dict[str, Any]) -> None:
    seen["updated_at"] = datetime.now(timezone.utc).isoformat()
    with SEEN_PATH.open("w", encoding="utf-8") as file:
        json.dump(seen, file, indent=2, sort_keys=True)
        file.write("\n")


def request_json(url: str, headers: dict[str, str], payload: dict[str, Any] | None = None) -> Any:
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
        headers = {**headers, "Content-Type": "application/json"}

    request = Request(url, data=data, headers=headers, method=method)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as error:
            message = error.read().decode("utf-8", errors="replace")
            is_rate_limit = error.code in {403, 429} and "rate limit" in message.lower()
            if method == "GET" and is_rate_limit and attempt < 2:
                wait_seconds = 90 * (attempt + 1)
                print(f"GitHub rate limit response. Waiting {wait_seconds} seconds before retrying.")
                time.sleep(wait_seconds)
                continue
            raise RuntimeError(f"HTTP {error.code} from {url}: {message}") from error
        except URLError as error:
            raise RuntimeError(f"Network error calling {url}: {error}") from error

    raise RuntimeError(f"Failed to call {url}.")


def github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "good-first-issue-tracker",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def search_github(labels: list[str], repos: list[str]) -> list[dict[str, Any]]:
    headers = github_headers()
    items: list[dict[str, Any]] = []

    if not repos:
        raise RuntimeError("Add at least one GitHub repository link to repos.json.")

    for repo in repos:
        params = urlencode(
            {
                "state": "open",
                "assignee": "none",
                "sort": "created",
                "direction": "desc",
                "per_page": "100",
            }
        )
        data = request_json(f"{GITHUB_API_URL}/repos/{repo}/issues?{params}", headers)
        for item in data:
            item["_tracked_repo"] = repo
            items.append(item)
        time.sleep(3.0)

    return items


def normalize_issue(raw: dict[str, Any]) -> Issue | None:
    pull_request = raw.get("pull_request")
    if pull_request:
        return None

    repo_url = raw.get("repository_url", "")
    repo = repo_url.rsplit("/", 2)[-2:]
    repo_name = raw.get("_tracked_repo") or ("/".join(repo) if len(repo) == 2 else "unknown/repository")
    number = int(raw.get("number", 0))
    key = f"{repo_name}#{number}"
    user = raw.get("user") or {}

    return Issue(
        key=key,
        title=raw.get("title", "(untitled issue)"),
        url=raw.get("html_url", ""),
        repo=repo_name,
        number=number,
        created_at=raw.get("created_at", ""),
        labels=[label.get("name", "") for label in raw.get("labels", [])],
        comments=int(raw.get("comments", 0)),
        author=user.get("login", "unknown"),
    )


def has_matching_label(issue: Issue, wanted_labels: list[str]) -> bool:
    wanted = {label.casefold() for label in wanted_labels}
    actual = {label.casefold() for label in issue.labels}
    return bool(wanted.intersection(actual))


def fresh_issues(raw_items: list[dict[str, Any]], seen: dict[str, Any], labels: list[str]) -> list[Issue]:
    exclude_repos = set(env_list("EXCLUDE_REPOS"))
    seen_issues = seen["issues"]
    deduped: dict[str, Issue] = {}

    for raw in raw_items:
        issue = normalize_issue(raw)
        if (
            not issue
            or issue.repo in exclude_repos
            or issue.key in seen_issues
            or not has_matching_label(issue, labels)
        ):
            continue
        deduped[issue.key] = issue

    issues = sorted(deduped.values(), key=lambda issue: issue.created_at, reverse=True)
    limit = int(os.getenv("MAX_ISSUES_PER_EMAIL") or "25")
    return issues[:limit]


def format_date(value: str) -> str:
    if not value:
        return "Unknown"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%b %-d, %Y at %-I:%M %p UTC")


def issue_card(issue: Issue) -> str:
    labels = "".join(f"<span>{html.escape(label)}</span>" for label in issue.labels[:6])
    return f"""
      <tr>
        <td class="card">
          <p class="repo">{html.escape(issue.repo)} <strong>#{issue.number}</strong></p>
          <h2><a href="{html.escape(issue.url)}">{html.escape(issue.title)}</a></h2>
          <p class="meta">Opened {html.escape(format_date(issue.created_at))} by @{html.escape(issue.author)} · {issue.comments} comments</p>
          <div class="labels">{labels}</div>
        </td>
      </tr>
    """


def build_email_html(issues: list[Issue]) -> str:
    cards = "\n".join(issue_card(issue) for issue in issues)
    count = len(issues)
    issue_word = "issue" if count == 1 else "issues"

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body {{
        margin: 0;
        background: #f5f7fa;
        color: #1f2937;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      .shell {{
        width: 100%;
        padding: 28px 0;
      }}
      .container {{
        width: min(680px, calc(100% - 32px));
        margin: 0 auto;
      }}
      .header {{
        background: #111827;
        color: #ffffff;
        padding: 28px;
        border-radius: 8px;
      }}
      .eyebrow {{
        margin: 0 0 10px;
        color: #6ee7b7;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: .04em;
        text-transform: uppercase;
      }}
      h1 {{
        margin: 0;
        font-size: 28px;
        line-height: 1.2;
      }}
      .subtitle {{
        margin: 12px 0 0;
        color: #d1d5db;
        font-size: 15px;
        line-height: 1.55;
      }}
      table {{
        width: 100%;
        border-collapse: separate;
        border-spacing: 0 14px;
      }}
      .card {{
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 22px;
      }}
      .repo {{
        margin: 0 0 8px;
        color: #047857;
        font-size: 13px;
        font-weight: 700;
      }}
      h2 {{
        margin: 0;
        font-size: 19px;
        line-height: 1.35;
      }}
      h2 a {{
        color: #111827;
        text-decoration: none;
      }}
      .meta {{
        margin: 10px 0 14px;
        color: #6b7280;
        font-size: 14px;
        line-height: 1.5;
      }}
      .labels span {{
        display: inline-block;
        margin: 0 8px 8px 0;
        padding: 5px 9px;
        border-radius: 999px;
        background: #ecfdf5;
        color: #065f46;
        font-size: 12px;
        font-weight: 650;
      }}
      .footer {{
        color: #6b7280;
        font-size: 13px;
        line-height: 1.55;
        text-align: center;
      }}
    </style>
  </head>
  <body>
    <div class="shell">
      <div class="container">
        <div class="header">
          <p class="eyebrow">Open source opportunities</p>
          <h1>{count} new beginner-friendly {issue_word}</h1>
          <p class="subtitle">Fresh open GitHub issues tagged good first issue, easy, beginner, or help wanted. Already-seen issues are skipped automatically.</p>
        </div>
        <table role="presentation">
          {cards}
        </table>
        <p class="footer">Sent by your free GitHub Actions tracker. The workflow runs every 5 hours and stores seen issue IDs in <code>seen.json</code>.</p>
      </div>
    </div>
  </body>
</html>"""


def send_email(issues: list[Issue]) -> None:
    api_key = os.getenv("RESEND_API_KEY")
    email_to = os.getenv("EMAIL_TO")
    email_from = os.getenv("EMAIL_FROM") or DEFAULT_EMAIL_FROM

    if not api_key or not email_to:
        raise RuntimeError("Missing RESEND_API_KEY or EMAIL_TO.")

    subject = f"{len(issues)} new good first issue{'s' if len(issues) != 1 else ''}"
    payload = {
        "from": email_from,
        "to": [email.strip() for email in email_to.split(",") if email.strip()],
        "subject": subject,
        "html": build_email_html(issues),
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    request_json(RESEND_EMAIL_URL, headers, payload)


def main() -> int:
    labels = env_list("SEARCH_LABELS", DEFAULT_LABELS)
    repos = load_repos()
    seen = load_seen()
    raw_items = search_github(labels, repos)
    issues = fresh_issues(raw_items, seen, labels)

    if not issues:
        print("No new matching issues found.")
        return 0

    send_email(issues)
    now = datetime.now(timezone.utc).isoformat()
    for issue in issues:
        seen["issues"][issue.key] = {
            "first_seen_at": now,
            "title": issue.title,
            "url": issue.url,
            "repo": issue.repo,
        }
    save_seen(seen)
    print(f"Emailed {len(issues)} new issue(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
