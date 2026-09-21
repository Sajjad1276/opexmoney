from __future__ import annotations

import asyncio
import ast
import asyncio
import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
ALLOWED_PREFIXES = ("app/", "ai/")
MAX_FILES = 6
MAX_SOURCE_CHARS = 14000
MAX_TOTAL_SOURCE_CHARS = 48000
MAX_CI_LOG_CHARS = 18000
DEFAULT_REPAIR_ATTEMPTS = 3
DEFAULT_CI_POLL_SECONDS = 8
_REPAIR_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class EngineeringResult:
    status: str
    summary: str
    branch: str | None = None
    pull_request: int | None = None
    changed_files: tuple[str, ...] = ()


def _github_token() -> str | None:
    return settings.support_github_token or settings.github_token


def _repo() -> str:
    return settings.support_github_repo.strip()


def _encoded(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _request_json_sync(path: str, *, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 30) -> dict[str, Any]:
    token = _github_token()
    if not token:
        raise RuntimeError("GitHub repair credential is not configured")
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "opex-money-smart-support",
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else {}


def _request_text_sync(path: str, *, timeout: float = 30) -> str:
    token = _github_token()
    if not token:
        raise RuntimeError("GitHub repair credential is not configured")
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "opex-money-smart-support",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc


async def _api(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(_request_json_sync, path, method=method, body=body)


async def _api_text(path: str) -> str:
    return await asyncio.to_thread(_request_text_sync, path)


def _safe_path(path: str) -> str:
    clean = path.replace("\\", "/").strip().lstrip("/")
    if not clean or clean.startswith("../") or "/../" in clean:
        raise ValueError("unsafe repository path")
    if clean in PROTECTED_FILES or any(
        clean == prefix[:-1] or clean.startswith(prefix)
        for prefix in PROTECTED_PREFIXES
    ):
        raise ValueError(f"protected path: {clean}")
    if not clean.startswith(ALLOWED_PREFIXES) or not clean.endswith(".py"):
        raise ValueError(f"unsupported patch path: {clean}")
    return clean


def _validate_python(content: str, path: str) -> None:
    try:
        ast.parse(content, filename=path)
    except SyntaxError as exc:
        raise ValueError(f"generated Python is invalid: {path}: {exc}") from exc


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


async def _generate_patch(*, report: str, traceback: str, event: str, source: dict[str, str], ci_failure: str, attempt: int) -> dict[str, Any]:
    client = companion._get_client()
    if client is None:
        raise RuntimeError("Gemini unavailable")

    source_text = "\n\n".join(
        f"FILE {path}\n{content}" for path, content in source.items()
    )
    prompt = f"""
You are the autonomous bug-fixing engineer for OPEX MONEY.
Fix only the reported software bug.

Rules:
- Read the supplied source before changing it.
- Make the smallest correct change.
- Do not add features.
- Do not change game economy rules.
- Do not grant money, assets, rewards, roles, permissions, or special treatment.
- Do not weaken validation, authorization, membership, locking, or transaction safety.
- Do not modify tests, migrations, CI, Docker, configuration, or the support system.
- Do not modify app/services/support/*.
- Do not expose secrets.
- Preserve existing public APIs unless required for the bug.
- Return complete file contents for every changed file.
- If no correct repair can be proven, return an empty files list.
- This is repair attempt {attempt} of {DEFAULT_REPAIR_ATTEMPTS}.

Return JSON only:
{{"summary":"brief reason","files":[{{"path":"app/...py","content":"complete file content"}}]}}

PLAYER REPORT:
{report[: settings.support_max_report_chars]}

LAST USER EVENT:
{event[:500]}

TRACEBACK:
{traceback[-8000:]}

CI FAILURE FROM PREVIOUS ATTEMPT:
{ci_failure[-MAX_CI_LOG_CHARS:]}

SOURCE:
{source_text}
"""
    response = await client.aio.models.generate_content(
        model=settings.ai_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=18000,
            response_mime_type="application/json",
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise ValueError("empty repair response")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("repair response is not an object")
    return payload


async def _create_branch(branch: str, sha: str) -> None:
    await _api(
        f"/repos/{_repo()}/git/refs",
        method="POST",
        body={"ref": f"refs/heads/{branch}", "sha": sha},
    )


async def _apply_changes(branch: str, changes: list[tuple[str, str]]) -> str:
    ref = await _api(f"/repos/{_repo()}/git/ref/heads/{_encoded(branch)}")
    parent_sha = str(ref["object"]["sha"])
    commit = await _api(f"/repos/{_repo()}/git/commits/{parent_sha}")
    base_tree = str(commit["tree"]["sha"])

    tree_items = []
    for path, content in changes:
        blob = await _api(
            f"/repos/{_repo()}/git/blobs",
            method="POST",
            body={"content": content, "encoding": "utf-8"},
        )
        tree_items.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": str(blob["sha"])}
        )

    tree = await _api(
        f"/repos/{_repo()}/git/trees",
        method="POST",
        body={"base_tree": base_tree, "tree": tree_items},
    )
    new_commit = await _api(
        f"/repos/{_repo()}/git/commits",
        method="POST",
        body={
            "message": "auto-fix: repair reported game bug",
            "tree": str(tree["sha"]),
            "parents": [parent_sha],
        },
    )
    commit_sha = str(new_commit["sha"])
    await _api(
        f"/repos/{_repo()}/git/refs/heads/{_encoded(branch)}",
        method="PATCH",
        body={"sha": commit_sha, "force": False},
    )
    return commit_sha


async def _create_pr(branch: str) -> tuple[int, str]:
    pr = await _api(
        f"/repos/{_repo()}/pulls",
        method="POST",
        body={
            "title": "Auto-fix reported OPEX MONEY bug",
            "head": branch,
            "base": "main",
            "body": (
                "Generated by OPEX MONEY Smart Support.\n\n"
                "Bug-only repair. The agent is restricted from editing tests, "
                "CI, migrations, configuration, or support code."
            ),
        },
    )
    return int(pr["number"]), str(pr["head"]["sha"])


async def _ci_failure_evidence(run_id: int) -> str:
    jobs = await _api(f"/repos/{_repo()}/actions/runs/{run_id}/jobs?per_page=100")
    chunks = []
    total = 0
    for job in jobs.get("jobs", []):
        if job.get("conclusion") not in {"failure", "cancelled", "timed_out"}:
            continue
        failed_steps = [
            str(step.get("name"))
            for step in job.get("steps", [])
            if step.get("conclusion") in {"failure", "cancelled", "timed_out"}
        ]
        try:
            logs = await _api_text(f"/repos/{_repo()}/actions/jobs/{int(job['id'])}/logs")
        except Exception as exc:
            logs = f"log retrieval failed: {exc}"
        block = (
            f"JOB: {job.get('name')}\n"
            f"FAILED STEPS: {', '.join(failed_steps) or 'unknown'}\n"
            f"LOG:\n{logs[-9000:]}"
        )
        chunks.append(block)
        total += len(block)
        if total >= MAX_CI_LOG_CHARS:
            break
    return "\n\n".join(chunks)[-MAX_CI_LOG_CHARS:]


async def _wait_for_ci(*, branch: str, head_sha: str, deadline: float) -> tuple[str, str]:
    while asyncio.get_running_loop().time() < deadline:
        try:
            runs_payload = await _api(
                f"/repos/{_repo()}/actions/runs"
                f"?head={urllib.parse.quote(branch, safe='')}&per_page=20"
            )
            runs = [
                run for run in runs_payload.get("workflow_runs", [])
                if str(run.get("head_sha") or "") == head_sha
            ]
        except Exception:
            runs = []

        if runs:
            active = [run for run in runs if run.get("status") != "completed"]
            if active:
                await asyncio.sleep(DEFAULT_CI_POLL_SECONDS)
                continue
            failures = [
                run for run in runs
                if run.get("conclusion") not in {"success", "neutral", "skipped"}
            ]
            if failures:
                return "failed", await _ci_failure_evidence(int(failures[0]["id"]))
            return "passed", ""

        try:
            checks = await _api(f"/repos/{_repo()}/commits/{head_sha}/check-runs")
            check_runs = checks.get("check_runs", [])
        except Exception:
            check_runs = []

        if check_runs:
            active = [run for run in check_runs if run.get("status") != "completed"]
            if active:
                await asyncio.sleep(DEFAULT_CI_POLL_SECONDS)
                continue
            failures = [
                run for run in check_runs
                if run.get("conclusion") not in {"success", "neutral", "skipped"}
            ]
            if failures:
                return "failed", json.dumps(
                    {
                        "check_run": failures[0].get("name"),
                        "conclusion": failures[0].get("conclusion"),
                        "output": failures[0].get("output", {}),
                    },
                    ensure_ascii=False,
                )
            return "passed", ""

        await asyncio.sleep(DEFAULT_CI_POLL_SECONDS)

    return "timeout", "CI did not finish before the repair deadline."


def _prepare_changes(payload: dict[str, Any], current_source: dict[str, str]) -> list[tuple[str, str]]:
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("repair payload has no files list")
    if len(raw_files) > MAX_FILES:
        raise ValueError("repair touched too many files")

    changes = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("invalid repair file entry")
        path = _safe_path(str(item.get("path", "")))
        replacement = item.get("content")
        if not isinstance(replacement, str) or not replacement.strip():
            raise ValueError(f"empty generated file: {path}")
        original = current_source.get(path, "")
        _validate_python(replacement, path)
        if not _policy_ok(original, replacement):
            raise ValueError(f"repair policy rejected: {path}")
        changes.append((path, replacement))

    if len({path for path, _ in changes}) != len(changes):
        raise ValueError("duplicate repair file paths")
    return changes

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


async def repair_code(*, report: str, traceback: str, event: str) -> EngineeringResult:
    if not settings.support_engineering_enabled:
        return EngineeringResult("disabled", "تعمیر خودکار کد غیرفعال است.")
    if not _github_token():
        return EngineeringResult(
            "credentials_missing",
            "دسترسی مهندسی GitHub برای اصلاح دائمی کد تنظیم نشده است.",
        )
    if _REPAIR_LOCK.locked():
        return EngineeringResult("busy", "یک عملیات تعمیر خودکار دیگر در حال اجراست.")

    await _REPAIR_LOCK.acquire()
    try:
        paths = _relevant_files(report, traceback, event)
        if not paths:
            return EngineeringResult("no_context", "فایل مرتبط برای اصلاح پیدا نشد.")

        base_sha, _ = await _base_ref()
        branch = f"support/autofix-{uuid.uuid4().hex[:10]}"
        await _create_branch(branch, base_sha)

        source = await _source_snapshot(paths, "main")
        if not source:
            return EngineeringResult(
                "no_context",
                "سورس مرتبط از GitHub قابل خواندن نیست.",
                branch=branch,
            )

        pr_number = None
        changed_files = set()
        ci_failure = ""
        deadline = asyncio.get_running_loop().time() + max(
            60,
            settings.support_engineering_timeout_seconds,
        )

        for attempt in range(1, DEFAULT_REPAIR_ATTEMPTS + 1):
            if asyncio.get_running_loop().time() >= deadline:
                return EngineeringResult(
                    "timeout",
                    "زمان تعمیر خودکار تمام شد و اصلاحیه وارد نسخه اصلی نشد.",
                    branch=branch,
                    pull_request=pr_number,
                    changed_files=tuple(sorted(changed_files)),
                )

            current_ref = "main" if attempt == 1 else branch
            source = await _source_snapshot(paths, current_ref)
            payload = await _generate_patch(
                report=report,
                traceback=traceback,
                event=event,
                source=source,
                ci_failure=ci_failure,
                attempt=attempt,
            )
            changes = _prepare_changes(payload, source)

            if not changes:
                return EngineeringResult(
                    "no_patch",
                    "هوش مصنوعی اصلاحیه قابل اثباتی برای این خطا تولید نکرد.",
                    branch=branch,
                    pull_request=pr_number,
                    changed_files=tuple(sorted(changed_files)),
                )

            head_sha = await _apply_changes(branch, changes)
            changed_files.update(path for path, _ in changes)

            if pr_number is None:
                pr_number, _ = await _create_pr(branch)

            ci_status, ci_failure = await _wait_for_ci(
                branch=branch,
                head_sha=head_sha,
                deadline=deadline,
            )

            if ci_status == "passed":
                if await _merge_pr(pr_number):
                    return EngineeringResult(
                        "fixed",
                        "باگ اصلاح شد، CI سبز شد و اصلاحیه وارد نسخه اصلی شد.",
                        branch=branch,
                        pull_request=pr_number,
                        changed_files=tuple(sorted(changed_files)),
                    )
                return EngineeringResult(
                    "merge_failed",
                    "اصلاحیه و تست‌ها موفق بودند اما merge خودکار انجام نشد.",
                    branch=branch,
                    pull_request=pr_number,
                    changed_files=tuple(sorted(changed_files)),
                )

            if ci_status == "timeout":
                return EngineeringResult(
                    "timeout",
                    "اصلاحیه ساخته شد اما CI در بازه تعیین‌شده تمام نشد.",
                    branch=branch,
                    pull_request=pr_number,
                    changed_files=tuple(sorted(changed_files)),
                )

        return EngineeringResult(
            "ci_failed",
            "اصلاحیه ساخته شد اما CI پس از چند تلاش همچنان شکست خورد و وارد نسخه اصلی نشد.",
            branch=branch,
            pull_request=pr_number,
            changed_files=tuple(sorted(changed_files)),
        )
    except Exception as exc:
        logger.exception("Support code repair failed")
        return EngineeringResult(
            "error",
            f"تعمیر خودکار با خطای داخلی متوقف شد: {type(exc).__name__}.",
        )
    finally:
        _REPAIR_LOCK.release()



__all__ = ["EngineeringResult", "repair_code"]
