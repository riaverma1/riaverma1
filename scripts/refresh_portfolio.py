#!/usr/bin/env python3
"""
scripts/refresh_portfolio.py

Collects data from all public GitHub repos (READMEs, recent commits, repo
metadata) then calls Claude to rewrite the project cards and small-builds
list inside index.html.

Run by the GitHub Action; can also be run locally if env vars are set.
"""

import os
import re
import json
import base64
import datetime
import requests
from openai import OpenAI

# ── Config ──────────────────────────────────────────────────────────────────

GITHUB_USERNAME  = os.environ["GITHUB_USERNAME"]      # riaverma1
GITHUB_TOKEN     = os.environ["GITHUB_TOKEN"]
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]

GITHUB_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Repos to always skip (forks, archived, boilerplate, etc.)
SKIP_REPOS = set()

# How many recent commits to pull per repo
COMMITS_TO_FETCH = 10

# ── GitHub helpers ───────────────────────────────────────────────────────────

def gh_get(url: str, params: dict = None) -> dict | list:
    resp = requests.get(url, headers=GITHUB_HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def fetch_repos() -> list[dict]:
    """Return all non-fork, non-archived public repos for the user."""
    repos = []
    page = 1
    while True:
        batch = gh_get(
            f"https://api.github.com/users/{GITHUB_USERNAME}/repos",
            params={"type": "owner", "sort": "pushed", "per_page": 100, "page": page},
        )
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return [
        r for r in repos
        if not r["fork"]
        and not r["archived"]
        and r["name"] not in SKIP_REPOS
    ]


def fetch_readme(repo_name: str) -> str:
    """Return decoded README text, or empty string if none."""
    try:
        data = gh_get(f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/readme")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except requests.HTTPError:
        return ""


def fetch_recent_commits(repo_name: str) -> list[str]:
    """Return a list of recent commit messages."""
    try:
        commits = gh_get(
            f"https://api.github.com/repos/{GITHUB_USERNAME}/{repo_name}/commits",
            params={"per_page": COMMITS_TO_FETCH},
        )
        return [c["commit"]["message"].split("\n")[0] for c in commits]
    except requests.HTTPError:
        return []


def build_repo_summary(repo: dict) -> dict:
    """Compile a single dict of everything we know about a repo."""
    name = repo["name"]
    return {
        "name": name,
        "description": repo.get("description") or "",
        "url": repo["html_url"],
        "homepage": repo.get("homepage") or "",
        "topics": repo.get("topics", []),
        "language": repo.get("language") or "",
        "stars": repo.get("stargazers_count", 0),
        "pushed_at": repo.get("pushed_at", ""),
        "readme_excerpt": fetch_readme(name)[:2000],   # first 2000 chars
        "recent_commits": fetch_recent_commits(name),
    }


# ── Claude call ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a concise technical writer helping maintain a developer portfolio page.
Given a list of GitHub repos (with READMEs and recent commits), you will return
ONLY a JSON object — no markdown fences, no preamble — with this exact shape:

{
  "featured": [
    {
      "title": "Short display title",
      "desc": "1–2 sentence description, plain English, no jargon overload.",
      "type": "case-study | live-build | wip",
      "url": "https://... or empty string"
    }
  ],
  "small_builds": [
    {
      "name": "Short name",
      "desc": "one-line description",
      "url": "https://... or empty string"
    }
  ]
}

Rules:
- featured: pick 3–5 repos that best showcase the owner's AI / data-science / supply-chain work.
  Prefer projects with a README, recent commits, or a live URL.
- small_builds: everything else that's interesting but smaller in scope.
- Keep descriptions warm and personal, first-person-adjacent (e.g. "Built to…", "A tool for…").
- type = "case-study" for analytical or research projects,
         "live-build" for deployed apps / demos,
         "wip" for clearly in-progress work.
- If a repo is purely boilerplate, a fork mirror, or has zero activity, omit it entirely.
- Return ONLY the JSON. No commentary.
""".strip()


def ask_openai(repo_summaries: list[dict]) -> dict:
    client = OpenAI(api_key=OPENAI_API_KEY)
    user_content = (
        "Here are my GitHub repos. Please update my portfolio accordingly.\n\n"
        + json.dumps(repo_summaries, indent=2)
    )
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
    )
    raw = response.choices[0].message.content.strip()
    # Strip accidental markdown fences just in case
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


# ── HTML patching ────────────────────────────────────────────────────────────

TYPE_LABELS = {
    "case-study": ("type-case", "Case study"),
    "live-build": ("type-build", "Live build"),
    "wip":        ("type-mini", "WIP"),
}

CARD_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<circle cx="12" cy="12" r="2"/>'
    '<circle cx="4" cy="6" r="2"/><circle cx="20" cy="6" r="2"/>'
    '<circle cx="4" cy="18" r="2"/><circle cx="20" cy="18" r="2"/>'
    '<line x1="6" y1="6" x2="10" y2="11"/>'
    '<line x1="18" y1="6" x2="14" y2="11"/>'
    '<line x1="6" y1="18" x2="10" y2="13"/>'
    '<line x1="18" y1="18" x2="14" y2="13"/>'
    '</svg>'
)

EXTERNAL_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    '<polyline points="15 3 21 3 21 9"/>'
    '<line x1="10" y1="14" x2="21" y2="3"/>'
    '</svg>'
)

ARROW_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<line x1="5" y1="12" x2="19" y2="12"/>'
    '<polyline points="12 5 19 12 12 19"/>'
    '</svg>'
)


def render_project_card(item: dict) -> str:
    type_key   = item.get("type", "case-study")
    css_class, label = TYPE_LABELS.get(type_key, ("type-case", "Case study"))
    url        = item.get("url") or "#"
    title      = item["title"]
    desc       = item["desc"]
    footer_txt = "view project" if url == "#" else "open →"

    return f"""
      <a class="project-card" href="{url}" target="_blank" rel="noopener" aria-label="{title}">
        <div class="card-top">
          <span class="card-type {css_class}">{label}</span>
          <span class="card-icon">{CARD_ICON_SVG}</span>
        </div>
        <p class="card-title">{title}</p>
        <p class="card-desc">{desc}</p>
        <div class="card-footer">
          {EXTERNAL_ICON_SVG}
          {footer_txt}
        </div>
      </a>""".strip()


def render_small_item(item: dict) -> str:
    url  = item.get("url") or "#"
    name = item["name"]
    desc = item.get("desc", "")
    return f"""
      <a class="small-item" href="{url}" target="_blank" rel="noopener">
        <div class="small-left">
          <span class="small-name">{name}</span>
          <span class="small-desc">{desc}</span>
        </div>
        <span class="small-arrow">{ARROW_SVG}</span>
      </a>""".strip()


def patch_html(html: str, data: dict) -> str:
    """Replace the project-grid and small-list sections in index.html."""

    # ── Featured project grid ─────────────────────────────────────────────
    cards_html = "\n\n      ".join(render_project_card(p) for p in data["featured"])
    new_grid = (
        f'<div class="project-grid">\n\n      {cards_html}\n\n    </div>'
    )
    html = re.sub(
        r'<div class="project-grid">.*?</div>',
        new_grid,
        html,
        flags=re.DOTALL,
    )

    # ── Small builds list ─────────────────────────────────────────────────
    items_html = "\n\n      ".join(render_small_item(s) for s in data["small_builds"])
    new_list = (
        f'<div class="small-list">\n\n      {items_html}\n\n    </div>'
    )
    html = re.sub(
        r'<div class="small-list">.*?</div>',
        new_list,
        html,
        flags=re.DOTALL,
    )

    # ── Stamp the footer year ─────────────────────────────────────────────
    year = datetime.date.today().year
    html = re.sub(
        r'(Ria Verma\s*·\s*)\d{4}',
        rf'\g<1>{year}',
        html,
    )

    return html


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("→ Fetching repos...")
    repos = fetch_repos()
    print(f"  Found {len(repos)} repos")

    print("→ Building summaries (READMEs + commits)...")
    summaries = []
    for repo in repos:
        print(f"  · {repo['name']}")
        summaries.append(build_repo_summary(repo))

    print("→ Asking Claude to update project cards...")
    data = ask_openai(summaries)
    print(f"  Featured: {len(data['featured'])}  |  Small builds: {len(data['small_builds'])}")

    print("→ Patching index.html...")
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    updated = patch_html(html, data)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(updated)

    print("✓ index.html updated.")


if __name__ == "__main__":
    main()
