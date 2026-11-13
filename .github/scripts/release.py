"""Publish a release for the current main commit.

Version rules:
- manifest.json version higher than the last vX.Y.Z tag (or no tag yet):
  that version is released as is (manual bump wins);
- otherwise it is bumped from the commits since the last tag:
  major if a commit says "BREAKING CHANGE" or uses "type!:",
  minor if a commit subject starts with "feat",
  patch otherwise.

Release notes (also prepended to CHANGELOG.md):
- one section per merged pull request: its title and its description
  (minus test/verification sections and the bot signature);
- commits pushed directly to main, grouped by type (feat, fix, docs…);
- breaking changes and a new minimum Home Assistant version highlighted;
- update instructions and a link to the full comparison.

Then: manifest.json + card version updated, commit "chore(release): vX.Y.Z",
tag, push, GitHub release with the same notes.

Usage:
  python .github/scripts/release.py [--dry-run]
  python .github/scripts/release.py --notes-for v1.2.0     # print notes of a tag
  python .github/scripts/release.py --rebuild-changelog    # rewrite CHANGELOG.md
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "custom_components" / "suivi_colis" / "manifest.json"
INIT = ROOT / "custom_components" / "suivi_colis" / "__init__.py"
CARD = ROOT / "custom_components" / "suivi_colis" / "www" / "suivi-colis-card.js"
HACS_JSON = ROOT / "hacs.json"
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE_PREFIX = "chore(release):"
MAX_COMMITS = 30

# Conventional commit type -> release notes section (in display order)
SECTIONS = [
    ("feat", "Nouveautés"),
    ("fix", "Corrections"),
    ("perf", "Performances"),
    ("docs", "Documentation"),
    ("maintenance", "Maintenance"),
    ("other", "Autres changements"),
]
MAINTENANCE_TYPES = {"ci", "chore", "refactor", "test", "tests", "build", "style"}
CONVENTIONAL = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<text>.+)$")
# PR description sections that are not useful to users
# Conventional "BREAKING CHANGE:" footer (a mere mention in the text does not count)
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:\s*(.+)", re.MULTILINE)
SKIPPED_SECTIONS = re.compile(
    r"^(tests?|vérifications?|verifications?|checks?|état des vérifications|au merge\b.*)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- git / GitHub


def run(*args: str, check: bool = True) -> str:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        sys.exit(f"{' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def repo_slug() -> str | None:
    """owner/name of the GitHub repository."""
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = run("git", "remote", "get-url", "origin", check=False)
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def github_api(path: str) -> dict | None:
    """GET on the GitHub API (token from GH_TOKEN when available)."""
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "release-script"},
    )
    if os.environ.get("GH_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GH_TOKEN']}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except Exception as err:  # offline, rate limit…: notes fall back to commits
        print(f"GitHub API {path}: {err}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- versions


def parse(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        sys.exit(f"Invalid version: {version!r}")
    return tuple(int(p) for p in match.groups())  # type: ignore[return-value]


def fmt(version: tuple[int, int, int]) -> str:
    return ".".join(map(str, version))


def tags() -> list[str]:
    """vX.Y.Z tags, newest first."""
    out = run("git", "tag", "--list", "v*", "--sort=-v:refname").splitlines()
    return [t for t in out if re.fullmatch(r"v\d+\.\d+\.\d+", t)]


def last_tag() -> str | None:
    all_tags = tags()
    return all_tags[0] if all_tags else None


def previous_tag(tag: str) -> str | None:
    all_tags = tags()
    idx = all_tags.index(tag)
    return all_tags[idx + 1] if idx + 1 < len(all_tags) else None


@dataclass
class Commit:
    sha: str
    subject: str
    body: str
    parents: list[str] = field(default_factory=list)

    @property
    def is_release(self) -> bool:
        return self.subject.startswith(RELEASE_PREFIX)

    @property
    def pr_number(self) -> int | None:
        """PR merged by this commit (merge commit or squash "(#N)")."""
        match = re.match(r"Merge pull request #(\d+)", self.subject) or re.search(
            r"\(#(\d+)\)$", self.subject
        )
        return int(match.group(1)) if match else None


def log(rev: str, *extra: str) -> list[Commit]:
    raw = run("git", "log", *extra, "--reverse", "--format=%H%x1f%P%x1f%s%x1f%b%x1e", rev)
    commits = []
    for entry in raw.split("\x1e"):
        if not entry.strip():
            continue
        sha, parents, subject, body = (entry.strip("\n").split("\x1f") + ["", "", ""])[:4]
        commits.append(Commit(sha, subject, body, parents.split()))
    return commits


def commits_since(tag: str | None, until: str = "HEAD") -> list[Commit]:
    """All non-release commits since tag (used for the version bump)."""
    rev = f"{tag}..{until}" if tag else until
    return [c for c in log(rev) if not c.is_release]


def bump_level(commits: list[Commit]) -> str:
    level = "patch"
    for c in commits:
        if BREAKING_FOOTER.search(c.body) or re.match(r"^\w+(\([^)]*\))?!:", c.subject):
            return "major"
        if re.match(r"^feat(\([^)]*\))?:", c.subject, re.IGNORECASE):
            level = "minor"
    return level


def next_version(manifest_version: str, tag: str | None, commits: list[Commit]) -> str:
    current = parse(manifest_version)
    if tag is None or current > parse(tag):
        return fmt(current)
    major, minor, patch = parse(tag)
    level = bump_level(commits)
    if level == "major":
        return fmt((major + 1, 0, 0))
    if level == "minor":
        return fmt((major, minor + 1, 0))
    return fmt((major, minor, patch + 1))


# ---------------------------------------------------------------- notes


def clean_pr_body(body: str) -> str:
    """PR description as release notes: headings demoted, noise removed."""
    out: list[str] = []
    skip_level = 0  # inside a skipped section of this heading level
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            if skip_level and level > skip_level:
                continue
            skip_level = 0
            title = re.sub(r"[`*]", "", heading.group(2)).strip()
            if SKIPPED_SECTIONS.match(title):
                skip_level = level
                continue
            out.append("#" * min(level + 1, 6) + " " + heading.group(2).strip())
            continue
        if skip_level:
            continue
        if "Generated with [Claude Code]" in line or line.startswith("🤖"):
            continue
        # Sentences about the PR workflow itself ("Suite de #2 (…).",
        # "Au merge, le workflow publiera vX.Y.Z.") mean nothing in a release
        line = re.sub(r"^Suite de #\d+[^)]*\)?\.?\s*", "", line)
        line = re.sub(r"\s*Au merge,? le workflow publiera\s+\**v\d+(?:\.\d+)*\**\.?", "", line)
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    text = "\n".join(out).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def commit_section(subject: str) -> tuple[str, str, bool]:
    """(section key, text without the type prefix, breaking)."""
    match = CONVENTIONAL.match(subject)
    if not match:
        return "other", subject, False
    ctype = match.group("type").lower()
    text = match.group("text")
    if match.group("scope"):
        text = f"{match.group('scope')} : {text}"
    key = ctype if ctype in {"feat", "fix", "perf", "docs"} else (
        "maintenance" if ctype in MAINTENANCE_TYPES else "other"
    )
    return key, text[:1].upper() + text[1:], bool(match.group("bang"))


def min_ha_change(since: str | None, until: str) -> str | None:
    """New minimum Home Assistant version if hacs.json changed it."""
    def read(rev: str) -> str | None:
        raw = run("git", "show", f"{rev}:hacs.json", check=False)
        try:
            return json.loads(raw).get("homeassistant") if raw else None
        except ValueError:
            return None

    new = read(until)
    old = read(since) if since else None
    return new if new and new != old and since else None


def commit_lines(commits: list[Commit], slug: str | None, heading: str) -> list[str]:
    """Commits grouped by type, one bullet each with a link."""
    grouped: dict[str, list[str]] = {}
    for c in commits:
        key, text, _ = commit_section(c.subject)
        link = f"[{c.sha[:7]}](https://github.com/{slug}/commit/{c.sha})" if slug else c.sha[:7]
        grouped.setdefault(key, []).append(f"- {text} ({link})")
    lines: list[str] = []
    for key, title in SECTIONS:
        if key in grouped:
            lines += [f"{heading} {title}", "", *grouped[key], ""]
    return lines


def build_notes(version: str, tag: str | None, until: str = "HEAD") -> str:
    """Release notes for the commits between tag and until."""
    slug = repo_slug()
    rev = f"{tag}..{until}" if tag else until
    first_parent = [c for c in log(rev, "--first-parent") if not c.is_release]
    all_commits = [c for c in log(rev) if not c.is_release]

    pr_merges = [c for c in first_parent if c.pr_number]
    direct = [c for c in first_parent if not c.pr_number and len(c.parents) < 2]
    if tag is None and pr_merges:
        direct = []  # first release: the PRs describe it, not the whole history

    lines: list[str] = []

    breaking = [c for c in all_commits if BREAKING_FOOTER.search(c.body) or commit_section(c.subject)[2]]
    min_ha = min_ha_change(tag, until)
    if breaking or min_ha:
        lines += ["## ⚠️ À lire avant de mettre à jour", ""]
        if min_ha:
            lines.append(f"- Home Assistant **{min_ha}** ou plus récent est maintenant nécessaire.")
        for c in breaking:
            note = BREAKING_FOOTER.search(c.body)
            lines.append(f"- {(note.group(1).strip() if note else commit_section(c.subject)[1])}")
        lines.append("")

    prs = [c.pr_number for c in pr_merges]
    for merge in pr_merges:
        number = merge.pr_number
        pr = github_api(f"repos/{slug}/pulls/{number}") if slug else None
        if not pr:
            lines += [f"## Pull request #{number}", ""]
        else:
            lines += [f"## {pr['title']} ([#{number}]({pr['html_url']}))", ""]
            body = clean_pr_body(pr.get("body") or "")
            if body:
                lines += [body, ""]
        # Every commit of the PR, in case the description missed some
        if len(merge.parents) == 2:
            pr_commits = [
                c for c in log(f"{merge.parents[0]}..{merge.parents[1]}", "--no-merges")
                if not c.is_release
            ]
            if pr_commits:
                lines += [
                    "<details>",
                    f"<summary>Détail des {len(pr_commits)} commits</summary>",
                    "",
                    *commit_lines(pr_commits, slug, "####"),
                    "</details>",
                    "",
                ]

    if direct:
        shown = direct[-MAX_COMMITS:]
        if prs:
            lines += ["## Autres modifications", ""]
        lines += commit_lines(shown, slug, "##" if not prs else "###")
        if len(direct) > len(shown):
            lines += [f"… et {len(direct) - len(shown)} modifications plus anciennes.", ""]

    if not prs and not direct:
        lines += ["Aucune modification fonctionnelle.", ""]

    lines += [
        "---",
        "",
        "**Mise à jour** : HACS → Suivi de Colis → Mettre à jour, puis redémarrer Home Assistant "
        "et recharger la page du navigateur pour obtenir la nouvelle carte.",
    ]
    if slug:
        compare = (
            f"https://github.com/{slug}/compare/{tag}...v{version}"
            if tag
            else f"https://github.com/{slug}/commits/v{version}"
        )
        lines.append(f"**Toutes les modifications** : {compare}")
    return "\n".join(lines).strip() + "\n"


def changelog_section(version: str, date: str, notes: str) -> str:
    # Demote the notes' headings under the "## vX.Y.Z" heading
    body = re.sub(r"^(#{1,5}) ", r"#\1 ", notes, flags=re.MULTILINE)
    return f"## v{version} — {date}\n\n{body.strip()}\n"


def write_changelog(sections: list[str], keep_existing: bool = True) -> None:
    header = "# Changelog\n\n"
    previous = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() and keep_existing else ""
    rest = previous[len(header):] if previous.startswith(header) else previous
    CHANGELOG.write_text(header + "\n".join(sections) + ("\n" + rest.lstrip() if rest.strip() else ""),
                         encoding="utf-8")


def update_files(version: str, notes: str) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["version"] = version
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Card cache-busting versions follow the integration version
    for path, pattern in (
        (INIT, r'CARD_JS_VERSION = "[^"]*"'),
        (CARD, r'const CARD_VERSION = "[^"]*"'),
    ):
        text = path.read_text(encoding="utf-8")
        new = re.sub(pattern, pattern.split(" = ")[0] + f' = "{version}"', text, count=1)
        path.write_text(new, encoding="utf-8")

    write_changelog([changelog_section(version, dt.date.today().isoformat(), notes)])


# ---------------------------------------------------------------- commands


def tag_date(tag: str) -> str:
    return run("git", "log", "-1", "--format=%cs", tag)


def notes_for(tag: str) -> str:
    return build_notes(tag.lstrip("v"), previous_tag(tag), until=tag)


def rebuild_changelog() -> None:
    sections = [changelog_section(t.lstrip("v"), tag_date(t), notes_for(t)) for t in tags()]
    write_changelog(sections, keep_existing=False)
    print(f"CHANGELOG.md rebuilt ({len(sections)} versions)")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if "--notes-for" in sys.argv:
        print(notes_for(sys.argv[sys.argv.index("--notes-for") + 1]))
        return
    if "--rebuild-changelog" in sys.argv:
        rebuild_changelog()
        return

    dry_run = "--dry-run" in sys.argv
    tag = last_tag()
    commits = commits_since(tag)
    if tag and not commits:
        print(f"Nothing to release since {tag}.")
        return

    manifest_version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    version = next_version(manifest_version, tag, commits)
    if run("git", "tag", "--list", f"v{version}"):
        sys.exit(f"Tag v{version} already exists.")
    notes = build_notes(version, tag)

    print(f"Last tag: {tag or '-'} | manifest: {manifest_version} | release: v{version}")
    print(notes)
    if dry_run:
        return

    update_files(version, notes)
    run("git", "add", str(MANIFEST), str(INIT), str(CARD), str(CHANGELOG))
    run("git", "commit", "-m", f"{RELEASE_PREFIX} v{version}")
    run("git", "tag", "-a", f"v{version}", "-m", f"v{version}")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    run("git", "push", "origin", f"HEAD:{branch}")
    run("git", "push", "origin", f"v{version}")

    notes_file = ROOT / ".release-notes.md"
    notes_file.write_text(notes, encoding="utf-8")
    run("gh", "release", "create", f"v{version}", "--title", f"v{version}",
        "--notes-file", str(notes_file), "--verify-tag")
    notes_file.unlink()
    print(f"Released v{version}")


if __name__ == "__main__":
    main()
