# Good First Issue Tracker

Free email alerts every 5 hours for newly opened beginner-friendly GitHub issues.

## How It Works

GitHub Actions runs every 5 hours, `scripts/track_issues.py` searches open GitHub issues, unseen matches are emailed with Resend, and `seen.json` is committed back to the repo so the same issue is not sent twice.

## Free Setup

1. Create a free Resend account and API key.
2. Use Resend's onboarding sender while testing. Later, you can verify your own sender domain in Resend.
3. Push this folder to a GitHub repository.
4. In GitHub, open `Settings -> Secrets and variables -> Actions`.
5. Add these repository secrets:

| Secret | Example | Notes |
| --- | --- | --- |
| `RESEND_API_KEY` | `re_...` | Your Resend API key. |
| `EMAIL_TO` | `you@example.com` | Comma-separated emails are supported. |

`GITHUB_TOKEN` is provided automatically by GitHub Actions.

By default, emails are sent from:

```text
Good First Issues <onboarding@resend.dev>
```

If you verify your own domain in Resend, you can optionally add this repository secret:

| Secret | Example | Notes |
| --- | --- | --- |
| `EMAIL_FROM` | `Issues <alerts@yourdomain.com>` | Optional custom sender. Must be allowed by Resend. |

## Repositories To Track

Edit `repos.json` and add GitHub repository links:

```json
[
  "https://github.com/clickhouse/clickhouse",
  "https://github.com/vercel/next.js",
  "https://github.com/facebook/react"
]
```

Only GitHub repository links are accepted.

If `repos.json` has at least one repository, the script tracks only those repositories. If it is empty, the script falls back to the optional `TRACK_REPOS` GitHub Actions variable.

## Optional Filters

Add repository variables under `Settings -> Secrets and variables -> Actions -> Variables`.

| Variable | Example | Default |
| --- | --- | --- |
| `SEARCH_LABELS` | `good first issue,easy,easy task,beginner` | Common beginner labels. |
| `TRACK_REPOS` | `facebook/react,vercel/next.js` | Fallback only when `repos.json` is empty. |
| `EXCLUDE_REPOS` | `owner/noisy-repo` | Empty. |
| `MAX_ISSUES_PER_EMAIL` | `25` | `25`. |

For the cleanest signal, add open-source projects you care about to `repos.json`. Leaving both `repos.json` and `TRACK_REPOS` empty works, but large public GitHub searches can be noisy.

## Run Manually

Open the workflow in GitHub Actions and choose **Run workflow**. The scheduled cron also runs every 5 hours:

```yaml
0 */5 * * *
```

## Local Test

You can test the script locally if the environment variables are set:

```bash
export RESEND_API_KEY="re_..."
export EMAIL_TO="you@example.com"
python3 scripts/track_issues.py
```

## Cost

This can run for free using GitHub Actions free minutes for public repositories, GitHub's built-in `GITHUB_TOKEN`, and Resend's free tier. Check each provider's current limits if you expect high volume.
