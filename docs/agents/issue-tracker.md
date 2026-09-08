# Issue Tracker

## GitHub Issues

This repo uses GitHub Issues for tracking work. Issues are managed through the
[GitHub CLI (`gh`)](https://cli.github.com/).

### Configuration

| Setting                    | Value                                         |
| -------------------------- | --------------------------------------------- |
| **Tracker**                | GitHub Issues                                 |
| **Remote**                 | `git@github.com:sumansaha/NSA_webservice.git` |
| **CLI**                    | `gh` (GitHub CLI)                             |
| **PRs as request surface** | off                                           |

### Usage

- Create issues: `gh issue create --title "..." --body "..."`
- List issues: `gh issue list --state open --limit 50`
- View issue: `gh issue view <number>`
- Close issue: `gh issue close <number>`

### Consumer Rules

Engineering skills that read from this tracker will use the `gh` CLI to query
and create issues. The "PRs as a request surface" flag is **off** by default —
enable it later if you want external PRs in the triage queue.
