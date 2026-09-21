from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from google.genai import types

from ai import companion
from config import settings

logger = logging.getLogger("opexmoney.support.engineering")

ROOT = Path(__file__).resolve().parents[3]
PROTECTED_PREFIXES = (
    ".github/",
    "migrations/",
    "alembic/",
    "tests/",
    "app/services/support/",
)
PROTECTED_FILES = {"config.py", "Dockerfile", "requirements.txt", "requirements-dev.txt"}


@dataclass(frozen=True)
class EngineeringResult:
    status: str
    summary: str
    branch: str | None = None
    pull_request: int | None = None
    changed_files: tuple[str, ...] = ()


def _github_token() -> str | None:
    return settings.support_github_token or settings.github_token


def _api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    token = _github_token()
    if not token:
        raise RuntimeError("GitHub repair credential is not configured")

    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _safe_path(path: str) -> str:
    clean = path.replace("\\", "/").lstrip("/")
    if clean.startswith("../") or "/../" in clean:
        raise ValueError("path traversal")
    if clean in PROTECTED_FILES or any(
        clean == prefix[:-1] or clean.startswith(prefix)
        for prefix in PROTECTED_PREFIXES
    ):
        raise ValueError(f"protected path: {clean}")
    if not clean.endswith(".py") or not clean.startswith(("app/", "ai/")):
        raise ValueError(f"unsupported patch path: {clean}")
    return clean


def _policy_ok(original: str, replacement: str, path: str) -> bool:
    added = []
    before = original.splitlines()
    after = replacement.splitlines()
    for line in after:
        if line not in before:
            added.append(line)

    added_text = "\n".join(added)
    forbidden = (
        "eval(",
        "exec(",
        "os.system(",
        "subprocess.",
        "pickle.loads(",
        "__import__(",
    )
    if any(token in added_text for token in forbidden):
        return False

    if re.search(
        r"(user_id|telegram_id|from_user\.id|chat\.id).*"
        r"(balance|xr_balance|treasury|reward|holding).*"
        r"(=|\+=|-=)",
        added_text,
    ):
        return False

    if re.search(
        r"(user_id|telegram_id|from_user\.id).*"
        r"(role|is_admin|is_founder|owner_id).*=",
        added_text,
    ):
        return False

    return True


def _relevant_files(report: str, traceback: str, event: str) -> list[Path]:
    blob = f"{report}\n{traceback}\n{event}"
    found: list[Path] = []
    for match in re.findall(r"(?:app|ai)/[A-Za-z0-9_./-]+\.py", blob):
        path = (ROOT / match).resolve()
        if path.exists() and ROOT in path.parents and path not in found:
            found.append(path)

    keys = []
    lower = blob.casefold()
    for key, names in {
        "market": ("market", "trade"),
        "بازار": ("market", "trade"),
        "nation": ("nation",),
        "ملت": ("nation",),
        "war": ("war",),
        "جنگ": ("war",),
        "treasury": ("treasury",),
        "خزانه": ("treasury",),
        "academy": ("academy",),
    }.items():
        if key in lower:
            keys.extend(names)

    for directory in (ROOT / "app", ROOT / "ai"):
        for path in directory.rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix().casefold()
            if path not in found and any(key in relative for key in keys):
                found.append(path)
    return found[:6]


async def _generate(report: str, traceback: str, event: str, files: list[Path]) -> dict:
    client = companion._get_client()
    if client is None:
        raise RuntimeError("Gemini unavailable")

    source = []
    for path in files:
        source.append(
            f"FILE {path.relative_to(ROOT).as_posix()}\n"
            f"{path.read_text(encoding='utf-8')}\n"
        )

    prompt = f"""
Fix one OPEX MONEY software bug.
Do not add features.
Do not change economy rules.
Do not grant money, assets, rewards, roles, permissions, or special treatment to the reporting user.
Do not weaken validation, authorization, membership, locking, or transaction safety.
Do not modify tests, migrations, CI, Docker, configuration, or the support system.
Make the smallest correct code change.

Return JSON:
{{"summary":"...","files":[{{"path":"app/...","content":"complete file"}}]}}

REPORT:
{report}

EVENT:
{event}

TRACEBACK:
{traceback[-8000:]}

SOURCE:
{"".join(source)}
"""
    response = await client.aio.models.generate_content(
        model=settings.ai_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=10000,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text or "{}")


def _create_branch(branch: str, sha: str) -> None:
    _api(
        f"/repos/{settings.support_github_repo}/git/refs",
        method="POST",
        body={"ref": f"refs/heads/{branch}", "sha": sha},
    )


def _blob(content: str) -> str:
    result = _api(
        f"/repos/{settings.support_github_repo}/git/blobs",
        method="POST",
        body={"content": content, "encoding": "utf-8"},
    )
    return result["sha"]


def _commit(branch: str, base_sha: str, base_tree: str, files: list[tuple[str, str]]) -> str:
    tree_items = [
        {"path": path, "mode": "100644", "type": "blob", "sha": _blob(content)}
        for path, content in files
    ]
    tree = _api(
        f"/repos/{settings.support_github_repo}/git/trees",
        method="POST",
        body={"base_tree": base_tree, "tree": tree_items},
    )
    commit = _api(
        f"/repos/{settings.support_github_repo}/git/commits",
        method="POST",
        body={
            "message": "auto-fix: repair reported game bug",
            "tree": tree["sha"],
            "parents": [base_sha],
        },
    )
    _api(
        f"/repos/{settings.support_github_repo}/git/refs/heads/{branch}",
        method="PATCH",
        body={"sha": commit["sha"], "force": False},
    )
    return commit["sha"]


async def repair_code(
    *,
    report: str,
    traceback: str,
    event: str,
) -> EngineeringResult:
    if not settings.support_engineering_enabled:
        return EngineeringResult("disabled", "تعمیر خودکار کد غیرفعال است.")

    token = _github_token()
    if not token:
        return EngineeringResult(
            "credentials_missing",
            "دسترسی مهندسی GitHub برای اصلاح دائمی کد تنظیم نشده است.",
        )

    files = _relevant_files(report, traceback, event)
    if not files:
        return EngineeringResult("no_context", "فایل مرتبط برای اصلاح پیدا نشد.")

    payload = await _generate(report, traceback, event, files)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        return EngineeringResult("invalid_patch", "اصلاحیه معتبر تولید نشد.")

    changes: list[tuple[str, str]] = []
    for item in raw_files:
        path = _safe_path(str(item.get("path", "")))
        replacement = item.get("content")
        if not isinstance(replacement, str):
            raise ValueError("invalid patch content")
        original = (ROOT / path).read_text(encoding="utf-8")
        if not _policy_ok(original, replacement, path):
            return EngineeringResult(
                "blocked",
                "اصلاحیه با قوانین جلوگیری از تقلب یا دور زدن منطق بازی ناسازگار بود.",
            )
        changes.append((path, replacement))

    base = _api(f"/repos/{settings.support_github_repo}/git/ref/heads/main")
    base_sha = base["object"]["sha"]
    commit = _api(
        f"/repos/{settings.support_github_repo}/git/commits/{base_sha}"
    )
    branch = f"support/autofix-{uuid.uuid4().hex[:10]}"

    _create_branch(branch, base_sha)
    _commit(branch, base_sha, commit["tree"]["sha"], changes)

    pr = _api(
        f"/repos/{settings.support_github_repo}/pulls",
        method="POST",
        body={
            "title": "Auto-fix reported OPEX MONEY bug",
            "head": branch,
            "base": "main",
            "body": (
                "Generated by OPEX MONEY Smart Support. "
                "Bug-only repair. No economy or user-specific mutation allowed."
            ),
        },
    )

    pr_number = int(pr["number"])
    deadline = asyncio.get_running_loop().time() + settings.support_engineering_timeout_seconds

    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(8)
        checks = _api(
            f"/repos/{settings.support_github_repo}/commits/{pr['head']['sha']}/check-runs"
        )
        runs = checks.get("check_runs", [])
        if not runs:
            continue
        if any(run.get("status") != "completed" for run in runs):
            continue
        if all(run.get("conclusion") == "success" for run in runs):
            merged = _api(
                f"/repos/{settings.support_github_repo}/pulls/{pr_number}/merge",
                method="PUT",
                body={"merge_method": "squash"},
            )
            if merged.get("merged"):
                return EngineeringResult(
                    "fixed",
                    "باگ اصلاح شد، CI سبز شد و اصلاحیه وارد نسخه اصلی شد.",
                    tuple(path for path, _ in changes),
                    branch=branch,
                    pull_request=pr_number,
                )
            return EngineeringResult(
                "merge_failed",
                "تست‌ها سبز شدند اما merge انجام نشد.",
                branch=branch,
                pull_request=pr_number,
            )
        return EngineeringResult(
            "ci_failed",
            "اصلاحیه به CI رسید اما تست‌ها شکست خوردند و وارد نسخه اصلی نشد.",
            branch=branch,
            pull_request=pr_number,
        )

    return EngineeringResult(
        "timeout",
        "اصلاحیه ساخته شد اما CI در بازه بررسی تمام نشد.",
        branch=branch,
        pull_request=pr_number,
    )


__all__ = ["EngineeringResult", "repair_code"]
