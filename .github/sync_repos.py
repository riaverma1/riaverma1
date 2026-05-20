#!/usr/bin/env python3
"""
Checks for public repos not yet in the README Selected work table.
For each new repo that has a README, asks Claude for a one-liner and appends a row.

Requires env vars: GITHUB_TOKEN, ANTHROPIC_API_KEY
"""

import os
import re
import json
import base64
import requests
import anthropic

GITHUB_USERNAME = "riaverma1"
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

GITHUB_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

BADGE_URLS = {
    "case study": "https://img.shields.io/badge/case%20study-7c3aed?style=flat-square",
    "research":   "https://img.shields.io/badge/research-0969da?style=flat-square",
    "live app":   "https://img.shields.io/badge/live%20app-2da44e?style=flat-square",
    "tool":       "https://img.shields.io/badge/tool-57606a?style=flat-square",
}

# Repos to never add (the profile repo itself)
SKIP_REPOS = {GITHUB_USERNAME}


def gh_get(url, params=None):
    r = requests.get(url, headers=GITHUB_HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_public_repos():
    repos, page = [], 1
    while True:
        batch = gh_get(
            f"https://api.github.com/users/{GITHUB_USERNAME}/repos",
            params={"type": "owner", "sort": "pushed", "per_page": 100, "page": page},
        )
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return [r for r in repos if not r["fork"] and not r["archived"] and r["name"] not in SKIP_REPOS]


def fetch_readme(repo_name):
    try:
        data = gh_get(f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/readme")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except requests.HTTPError:
        return ""


def already_in_readme(readme_content, repo_name):
    """True if the repo name or its GitHub URL appears anywhere in the README."""
    return repo_name in readme_content or f"/{repo_name}" in readme_content


def ask_claude(repo_name, description, readme_excerpt, homepage):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = f"""Ria Verma is a Senior Data Scientist (AI, supply chain, operations). She has a new public GitHub repo. Write a one-liner for her portfolio README table.

Repo: {repo_name}
GitHub description: {description or "(none)"}
Homepage: {homepage or "(none)"}
README excerpt:
{readme_excerpt[:1500]}

Return JSON with exactly these fields:
- "one_liner": one short sentence under 12 words describing what it does
- "type": one of "case study", "research", "live app", "tool"
- "link_text": 2–3 words for the link, e.g. "repo →", "demo →", "case study →"

Return only valid JSON, no markdown."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = re.sub(r"^```[a-z]*\n?|```$", "", msg.content[0].text.strip())
    return json.loads(raw)


def build_row(repo, info):
    display = repo["name"].replace("-", " ").replace("_", " ").title()
    badge = BADGE_URLS.get(info["type"], BADGE_URLS["tool"])
    return (
        f'| **{display}** '
        f'| {info["one_liner"]} '
        f'| ![{info["type"]}]({badge}) '
        f'| [{info["link_text"]}]({repo["html_url"]}) |'
    )


def append_row_to_table(readme_content, new_row):
    """Insert new_row after the last data row in the Selected work table."""
    lines = readme_content.split("\n")
    last_data_row = -1
    in_section = False

    for i, line in enumerate(lines):
        if "### Selected work" in line:
            in_section = True
        if in_section:
            if line.startswith("|") and "---|" not in line and "Project" not in line:
                last_data_row = i
            if line.startswith("---") and last_data_row > 0:
                break

    if last_data_row == -1:
        return readme_content + "\n" + new_row

    lines.insert(last_data_row + 1, new_row)
    return "\n".join(lines)


def main():
    with open("README.md", encoding="utf-8") as f:
        readme = f.read()

    repos = fetch_public_repos()
    print(f"→ Found {len(repos)} public repos")

    added = 0
    for repo in repos:
        name = repo["name"]
        if already_in_readme(readme, name):
            print(f"  · {name}: already listed, skipping")
            continue

        readme_text = fetch_readme(name)
        if not readme_text:
            print(f"  · {name}: no README, skipping")
            continue

        print(f"  · {name}: new — asking Claude...")
        info = ask_claude(name, repo.get("description", ""), readme_text, repo.get("homepage", ""))
        print(f"    → {info}")
        readme = append_row_to_table(readme, build_row(repo, info))
        added += 1

    if added == 0:
        print("✓ No new repos to add.")
        return

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme)
    print(f"✓ Added {added} row(s) to README.md.")


if __name__ == "__main__":
    main()
