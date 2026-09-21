from __future__ import annotations

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
PROTECTED_FILES = {
    "config.py",
    "Dockerfile",
    "requirements.txt",
    "requirements-dev.txt",
}
ALLOWED_PREFIXES = ("app/", "ai/")

MAX_FILES = 8
MAX_CONTEXT_FILES = 28
MAX_SOURCE_CHARS = 26000
MAX_TOTAL_SOURCE_CHARS = 120000
MAX_CODEBASE_MAP_CHARS = 24000
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


def _request_json_sync(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    token = _github_token()
    if not token:
        raise RuntimeError("GitHub repair credential is not configured")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "opex-money-smart-support",
    }
    data = (
        json.dumps(body, ensure_ascii=False).encode("utf-8")
        if body is not None
        else None
    )
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        headers=headers,
        method=method,
    )

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

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "opex-money-smart-support",
    }
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers=headers,
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc


async def _api(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _request_json_sync,
        path,
        method=method,
        body=body,
    )


async def _api_text(path: str) -> str:
    return await asyncio.to_thread(_request_text_sync, path)


def _safe_path(path: str) -> str:
    clean = path.replace("\\", "/").strip().lstrip("/")
    if not clean or clean.startswith("../") or "/../" in clean:
        raise ValueError("unsafe repository path")
    if clean in PROTECTED_FILES:
        raise ValueError(f"protected path: {clean}")
    if any(
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


def _policy_ok(original: str, replacement: str) -> bool:
    before = original.splitlines()
    added = [line for line in replacement.splitlines() if line not in before]
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


def _relevant_paths(report: str, traceback: str, event: str) -> list[str]:
    blob = f"{report}\n{traceback}\n{event}"
    found: list[str] = []

    for match in re.findall(r"(?:app|ai)/[A-Za-z0-9_./-]+\.py", blob):
        clean = match.replace("\\", "/")
        try:
            _safe_path(clean)
        except ValueError:
            continue
        if clean not in found:
            found.append(clean)

    lower = blob.casefold()
    groups = {
        "market": ("market", "trade"),
        "بازار": ("market", "trade"),
        "nation": ("nation",),
        "ملت": ("nation",),
        "war": ("war",),
        "جنگ": ("war",),
        "treasury": ("treasury",),
        "خزانه": ("treasury",),
        "academy": ("academy",),
        "آکادمی": ("academy",),
        "portfolio": ("portfolio",),
        "دارایی": ("portfolio",),
    }

    keys: set[str] = set()
    for keyword, names in groups.items():
        if keyword in lower:
            keys.update(names)

    for directory in (ROOT / "app", ROOT / "ai"):
        if not directory.exists():
            continue
        for path in directory.rglob("*.py"):
            clean = path.relative_to(ROOT).as_posix()
            try:
                _safe_path(clean)
            except ValueError:
                continue
            folded = clean.casefold()
            if clean not in found and any(key in folded for key in keys):
                found.append(clean)

    return found[:MAX_FILES]


async def _base_ref() -> str:
    result = await _api(f"/repos/{_repo()}/git/ref/heads/main")
    return str(result["object"]["sha"])


async def _read_repo_file(path: str, ref: str) -> str | None:
    clean = _safe_path(path)
    encoded_path = "/".join(_encoded(part) for part in clean.split("/"))
    try:
        result = await _api(
            f"/repos/{_repo()}/contents/{encoded_path}?ref={_encoded(ref)}"
        )
    except RuntimeError as exc:
        if "GitHub API 404" in str(exc):
            return None
        raise

    encoded_content = result.get("content")
    if not isinstance(encoded_content, str):
        return None

    return base64.b64decode(encoded_content.encode("ascii")).decode("utf-8")


async def _repository_python_paths(ref: str) -> list[str]:
    ref_payload = await _api(
        f"/repos/{_repo()}/git/ref/heads/{_encoded(ref)}"
    )
    commit_sha = str(ref_payload["object"]["sha"])
    commit = await _api(f"/repos/{_repo()}/git/commits/{commit_sha}")
    tree_sha = str(commit["tree"]["sha"])
    tree = await _api(
        f"/repos/{_repo()}/git/trees/{_encoded(tree_sha)}?recursive=1"
    )
    paths = [
        str(item.get("path"))
        for item in tree.get("tree", [])
        if item.get("type") == "blob"
        and str(item.get("path", "")).endswith(".py")
        and str(item.get("path", "")).startswith(("app/", "ai/"))
    ]
    return sorted(paths)


def _resolve_local_imports(path: str, content: str, known_paths: set[str]) -> list[str]:
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError:
        return []

    module = path[:-3].replace("/", ".")
    package = module.rsplit(".", 1)[0] if "." in module else ""
    candidates: list[str] = []

    def add(module_name: str) -> None:
        if not module_name.startswith(("app.", "ai.")):
            return
        direct = module_name.replace(".", "/") + ".py"
        init = module_name.replace(".", "/") + "/__init__.py"
        for candidate in (direct, init):
            if candidate in known_paths and candidate not in candidates:
                candidates.append(candidate)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".") if package else []
                if node.level > len(base_parts) + 1:
                    continue
                parent = base_parts[: len(base_parts) - (node.level - 1)]
                imported = ".".join((*parent, *(node.module.split(".") if node.module else ())))
                if imported:
                    add(imported)
                    for alias in node.names:
                        add(f"{imported}.{alias.name}")
            elif node.module:
                add(node.module)
                for alias in node.names:
                    add(f"{node.module}.{alias.name}")

    return candidates


async def _expand_code_context(
    seed_paths: list[str],
    ref: str,
) -> tuple[list[str], str]:
    known_paths = set(await _repository_python_paths(ref))
    selected: list[str] = []

    def add(path: str) -> None:
        try:
            clean = _safe_path(path)
        except ValueError:
            return
        if clean in known_paths and clean not in selected:
            selected.append(clean)

    for path in seed_paths:
        add(path)

    frontier = list(selected)
    for _ in range(2):
        if len(selected) >= MAX_CONTEXT_FILES:
            break

        snapshot = await _source_snapshot(frontier, ref)
        next_frontier: list[str] = []
        for path, source in snapshot.items():
            for dependency in _resolve_local_imports(path, source, known_paths):
                before = len(selected)
                add(dependency)
                if len(selected) > before:
                    next_frontier.append(dependency)
                if len(selected) >= MAX_CONTEXT_FILES:
                    break
            if len(selected) >= MAX_CONTEXT_FILES:
                break

        frontier = next_frontier
        if not frontier:
            break

    seed_lower = " ".join(seed_paths).casefold()
    families = []
    for family in ("market", "trade", "nation", "war", "treasury", "academy", "portfolio"):
        if family in seed_lower:
            families.append(family)

    for path in known_paths:
        folded = path.casefold()
        if (
            len(selected) < MAX_CONTEXT_FILES
            and path not in selected
            and any(f"/{family}" in folded or f"_{family}" in folded for family in families)
        ):
            add(path)

    for preferred in (
        "app/database/models.py",
        "app/database/session.py",
        "app/db/models.py",
        "app/db/session.py",
        "app/services/trade.py",
        "app/services/market.py",
        "app/services/nation.py",
        "app/services/portfolio.py",
        "app/keyboards/inline.py",
    ):
        if len(selected) >= MAX_CONTEXT_FILES:
            break
        add(preferred)

    map_lines = "\n".join(known_paths)
    return selected[:MAX_CONTEXT_FILES], map_lines[:MAX_CODEBASE_MAP_CHARS]


async def _source_snapshot(paths: list[str], ref: str) -> dict[str, str]:
    source: dict[str, str] = {}
    total = 0

    results = await asyncio.gather(
        *(_read_repo_file(path, ref) for path in paths),
        return_exceptions=True,
    )
    for path, result in zip(paths, results):
        if isinstance(result, Exception) or result is None:
            continue

        file_content = str(result)
        if len(file_content) > MAX_SOURCE_CHARS:
            file_content = (
                file_content[:MAX_SOURCE_CHARS]
                + "\n# [source truncated by support agent]"
            )

        if total + len(file_content) > MAX_TOTAL_SOURCE_CHARS:
            break

        source[path] = file_content
        total += len(file_content)

    return source


async def _generate_patch(
    *,
    report: str,
    traceback: str,
    event: str,
    source: dict[str, str],
    codebase_map: str,
    ci_failure: str,
    attempt: int,
) -> dict[str, Any]:
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
- You have read-only access to the broader OPEX MONEY codebase, not only the files named by the traceback.
- Trace the reported failure through handlers, services, database models, keyboards, utilities, and local imports before deciding the root cause.
- Use the codebase map to discover additional files you need to inspect.
- Do not decide that a formatting-only change is a repair unless the failure is actually formatting-related.
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
{{"summary":"root cause and why this patch fixes it","files":[{{"path":"app/...py","content":"complete file content"}}]}}

CODEBASE MAP:
{codebase_map}

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


async def _apply_changes(
    branch: str,
    changes: list[tuple[str, str]],
) -> str:
    ref = await _api(f"/repos/{_repo()}/git/ref/heads/{_encoded(branch)}")
    parent_sha = str(ref["object"]["sha"])

    commit = await _api(f"/repos/{_repo()}/git/commits/{parent_sha}")
    base_tree = str(commit["tree"]["sha"])

    tree_items: list[dict[str, str]] = []
    for path, content in changes:
        blob = await _api(
            f"/repos/{_repo()}/git/blobs",
            method="POST",
            body={"content": content, "encoding": "utf-8"},
        )
        tree_items.append(
            {
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": str(blob["sha"]),
            }
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


async def _create_pr(branch: str) -> int:
    result = await _api(
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
    return int(result["number"])


async def _ci_failure_evidence(run_id: int) -> str:
    jobs = await _api(
        f"/repos/{_repo()}/actions/runs/{run_id}/jobs?per_page=100"
    )

    blocks: list[str] = []
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
            logs = await _api_text(
                f"/repos/{_repo()}/actions/jobs/{int(job['id'])}/logs"
            )
        except Exception as exc:
            logs = f"log retrieval failed: {exc}"

        block = (
            f"JOB: {job.get('name')}\n"
            f"FAILED STEPS: {', '.join(failed_steps) or 'unknown'}\n"
            f"LOG:\n{logs[-9000:]}"
        )
        blocks.append(block)
        total += len(block)

        if total >= MAX_CI_LOG_CHARS:
            break

    return "\n\n".join(blocks)[-MAX_CI_LOG_CHARS:]


async def _wait_for_ci(
    *,
    branch: str,
    head_sha: str,
    deadline: float,
) -> tuple[str, str]:
    while asyncio.get_running_loop().time() < deadline:
        try:
            runs_payload = await _api(
                f"/repos/{_repo()}/actions/runs"
                f"?head={_encoded(branch)}&per_page=20"
            )
            runs = [
                run
                for run in runs_payload.get("workflow_runs", [])
                if str(run.get("head_sha") or "") == head_sha
            ]
        except Exception:
            runs = []

        if runs:
            active = [
                run for run in runs if run.get("status") != "completed"
            ]
            if active:
                await asyncio.sleep(DEFAULT_CI_POLL_SECONDS)
                continue

            failures = [
                run
                for run in runs
                if run.get("conclusion")
                not in {"success", "neutral", "skipped"}
            ]
            if failures:
                return "failed", await _ci_failure_evidence(
                    int(failures[0]["id"])
                )
            return "passed", ""

        try:
            check_payload = await _api(
                f"/repos/{_repo()}/commits/{head_sha}/check-runs"
            )
            check_runs = check_payload.get("check_runs", [])
        except Exception:
            check_runs = []

        if check_runs:
            active = [
                run for run in check_runs if run.get("status") != "completed"
            ]
            if active:
                await asyncio.sleep(DEFAULT_CI_POLL_SECONDS)
                continue

            failures = [
                run
                for run in check_runs
                if run.get("conclusion")
                not in {"success", "neutral", "skipped"}
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


def _prepare_changes(
    payload: dict[str, Any],
    current_source: dict[str, str],
) -> list[tuple[str, str]]:
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise ValueError("repair payload has no files list")
    if len(raw_files) > MAX_FILES:
        raise ValueError("repair touched too many files")

    changes: list[tuple[str, str]] = []
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


async def _merge_pr(pr_number: int) -> bool:
    result = await _api(
        f"/repos/{_repo()}/pulls/{pr_number}/merge",
        method="PUT",
        body={"merge_method": "squash"},
    )
    return bool(result.get("merged"))


async def repair_code(
    *,
    report: str,
    traceback: str,
    event: str,
) -> EngineeringResult:
    if not settings.support_engineering_enabled:
        return EngineeringResult(
            "disabled",
            "تعمیر خودکار کد غیرفعال است.",
        )

    if not _github_token():
        return EngineeringResult(
            "credentials_missing",
            "دسترسی مهندسی GitHub برای اصلاح دائمی کد تنظیم نشده است.",
        )

    if _REPAIR_LOCK.locked():
        return EngineeringResult(
            "busy",
            "یک عملیات تعمیر خودکار دیگر در حال اجراست.",
        )

    await _REPAIR_LOCK.acquire()

    try:
        paths = _relevant_paths(report, traceback, event)
        if not paths:
            return EngineeringResult(
                "no_context",
                "فایل مرتبط برای اصلاح پیدا نشد.",
            )

        base_sha = await _base_ref()
        branch = f"support/autofix-{uuid.uuid4().hex[:10]}"
        await _create_branch(branch, base_sha)

        context_paths, codebase_map = await _expand_code_context(paths, "main")
        source = await _source_snapshot(context_paths, "main")
        if not source:
            return EngineeringResult(
                "no_context",
                "سورس مرتبط از GitHub قابل خواندن نیست.",
                branch=branch,
            )

        pr_number: int | None = None
        changed_files: set[str] = set()
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
            context_paths, codebase_map = await _expand_code_context(paths, current_ref)
            source = await _source_snapshot(context_paths, current_ref)

            payload = await _generate_patch(
                report=report,
                traceback=traceback,
                event=event,
                source=source,
                codebase_map=codebase_map,
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
                pr_number = await _create_pr(branch)

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
