from __future__ import annotations

import ast
import asyncio
import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from google.genai import types
from sqlalchemy import select

from ai import companion
from app.database.models import GovernanceLedger, User
from app.database.session import async_session
from config import settings

logger = logging.getLogger("opex.owner_ai")

ROOT_REPO = "Sajjad1276/opexmoney"
MAX_CONTEXT_FILES = 18
MAX_FILE_CHARS = 24000
MAX_TOTAL_CONTEXT = 100000
MAX_PLAN_ROUNDS = 6
MAX_FIX_ATTEMPTS = 3
CI_POLL_SECONDS = 8
DEFAULT_TIMEOUT = 420

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class OwnerAIResult:
    status: str
    summary: str
    branch: str | None = None
    pull_request: int | None = None
    changed_files: tuple[str, ...] = ()
    ci: str | None = None
    railway: str | None = None


def owner_ids() -> set[int]:
    values: set[int] = set()
    if settings.owner_id is not None:
        values.add(int(settings.owner_id))

    raw = os.getenv("ADMIN_USER_IDS", "")
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError:
            continue
    return values


def is_owner(user_id: int) -> bool:
    if not settings.owner_ai_enabled:
        return False
    return int(user_id) in owner_ids()


def _github_token() -> str | None:
    return settings.support_github_token or settings.github_token


def _railway_token() -> str | None:
    return (
        settings.railway_api_token
        or os.getenv("RAILWAY_API_TOKEN")
        or os.getenv("RAILWAY_TOKEN")
        or os.getenv("RAILWAY_PROJECT_TOKEN")
    )


def _repo() -> str:
    return settings.support_github_repo.strip() or ROOT_REPO


def _encode_path(path: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))


def _safe_repo_path(path: str) -> str:
    clean = path.replace("\\", "/").strip().lstrip("/")
    if not clean or clean.startswith("../") or "/../" in clean:
        raise ValueError("مسیر فایل معتبر نیست.")
    forbidden_names = {
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "service-account.json",
    }
    parts = PurePosixPath(clean).parts
    lower = clean.casefold()
    if any(part in forbidden_names for part in parts):
        raise ValueError("فایل‌های حاوی secret قابل دسترسی نیستند.")
    if any(token in lower for token in (".pem", ".key", "secret", "credentials")):
        raise ValueError("فایل‌های حساس قابل دسترسی نیستند.")
    if clean.startswith(".git/"):
        raise ValueError("مسیر داخلی Git قابل دسترسی نیست.")
    return clean


def _validate_generated_file(path: str, content: str) -> None:
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix == ".py":
        try:
            ast.parse(content, filename=path)
        except SyntaxError as exc:
            raise ValueError(f"Python تولیدشده معتبر نیست: {path}: {exc}") from exc

    forbidden = (
        "os.system(",
        "eval(",
        "exec(",
        "pickle.loads(",
    )
    if suffix == ".py" and any(item in content for item in forbidden):
        raise ValueError(f"کد تولیدشده شامل primitive ممنوع است: {path}")


def _request_json_sync(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    token = _github_token()
    if not token:
        raise RuntimeError("SUPPORT_GITHUB_TOKEN / GITHUB_TOKEN تنظیم نشده است.")

    payload = (
        json.dumps(body, ensure_ascii=False).encode("utf-8")
        if body is not None
        else None
    )
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "opex-owner-ai",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc
    return json.loads(raw) if raw else {}


def _request_text_sync(path: str, *, timeout: float = 30) -> str:
    token = _github_token()
    if not token:
        raise RuntimeError("GitHub credential تنظیم نشده است.")
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "opex-owner-ai",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc


async def _github(
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


async def _github_text(path: str) -> str:
    return await asyncio.to_thread(_request_text_sync, path)


async def _read_file(path: str, ref: str = "main") -> str:
    clean = _safe_repo_path(path)
    result = await _github(
        f"/repos/{_repo()}/contents/{_encode_path(clean)}?ref={urllib.parse.quote(ref, safe='')}"
    )
    content = result.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"محتوای {clean} قابل خواندن نیست.")
    decoded = base64.b64decode(content.encode("ascii")).decode("utf-8")
    return decoded[:MAX_FILE_CHARS]


async def _repo_tree(ref: str = "main") -> list[str]:
    ref_data = await _github(
        f"/repos/{_repo()}/git/ref/heads/{urllib.parse.quote(ref, safe='')}"
    )
    commit_sha = str(ref_data["object"]["sha"])
    commit = await _github(f"/repos/{_repo()}/git/commits/{commit_sha}")
    tree_sha = str(commit["tree"]["sha"])
    tree = await _github(
        f"/repos/{_repo()}/git/trees/{urllib.parse.quote(tree_sha, safe='')}?recursive=1"
    )
    return [
        str(item["path"])
        for item in tree.get("tree", [])
        if item.get("type") == "blob"
        and not str(item.get("path", "")).startswith(".git/")
    ]


async def _search_code(query: str) -> list[str]:
    result = await _github(
        f"/search/code?q={urllib.parse.quote(query + ' repo:' + _repo(), safe='')}"
    )
    return [
        str(item.get("path"))
        for item in result.get("items", [])[:20]
        if item.get("path")
    ]


async def _ci_status(branch: str | None = None) -> tuple[str, str]:
    suffix = f"&head={urllib.parse.quote(branch, safe='')}" if branch else ""
    result = await _github(
        f"/repos/{_repo()}/actions/runs?per_page=10{suffix}"
    )
    runs = result.get("workflow_runs", [])
    if not runs:
        return "unknown", "هیچ اجرای CI پیدا نشد."
    latest = runs[0]
    return (
        str(latest.get("conclusion") or latest.get("status") or "unknown"),
        str(latest.get("html_url") or latest.get("id") or ""),
    )


async def _ci_logs(run_id: int) -> str:
    jobs = await _github(
        f"/repos/{_repo()}/actions/runs/{run_id}/jobs?per_page=100"
    )
    blocks: list[str] = []
    for job in jobs.get("jobs", []):
        if job.get("conclusion") not in {"failure", "cancelled", "timed_out"}:
            continue
        try:
            log = await _github_text(
                f"/repos/{_repo()}/actions/jobs/{int(job['id'])}/logs"
            )
        except Exception as exc:
            log = f"log unavailable: {exc}"
        blocks.append(
            f"JOB {job.get('name')}\n"
            f"{log[-9000:]}"
        )
    return "\n\n".join(blocks)[-18000:]


async def _railway_status() -> str:
    token = _railway_token()
    if not token:
        return (
            "توکن Railway برای Agent تنظیم نشده است. "
            "پس از merge، Railway به‌صورت خودکار deploy می‌شود، "
            "اما Agent فعلاً status/runtime log آن را مستقیم نمی‌بیند."
        )

    endpoint = "https://backboard.railway.com/graphql/v2"
    query = """
    query ProjectState($projectId: String!) {
      project(id: $projectId) {
        name
        services {
          edges {
            node {
              name
              serviceInstances {
                edges {
                  node {
                    latestDeployment {
                      id
                      status
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    project_id = (
        os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_PROJECT")
        or ""
    )
    if not project_id:
        return "RAILWAY_PROJECT_ID تنظیم نشده است."

    headers = {
        "Content-Type": "application/json",
    }
    if os.getenv("RAILWAY_PROJECT_TOKEN"):
        headers["Project-Access-Token"] = token
    else:
        headers["Authorization"] = f"Bearer {token}"

    payload = json.dumps(
        {"query": query, "variables": {"projectId": project_id}}
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers=headers,
    )
    try:
        raw = await asyncio.to_thread(
            lambda: urllib.request.urlopen(request, timeout=20).read().decode(
                "utf-8",
                errors="replace",
            )
        )
    except Exception as exc:
        return f"Railway API در دسترس نیست: {type(exc).__name__}"

    parsed = json.loads(raw)
    if parsed.get("errors"):
        return "Railway API خطا داد."
    return json.dumps(parsed.get("data", {}), ensure_ascii=False)[:5000]


async def _audit(
    owner_id: int,
    action: str,
    *,
    rule_key: str,
    reason: str,
    old_value: str | None = None,
    new_value: str | None = None,
) -> None:
    try:
        async with async_session() as session:
            actor_id = await session.scalar(
                select(User.user_id).where(User.user_id == owner_id).limit(1)
            )
            session.add(
                GovernanceLedger(
                    at=datetime.now(UTC),
                    actor_player_id=actor_id,
                    action=action[:40],
                    rule_key=rule_key[:100],
                    old_value=(old_value or "")[:64] or None,
                    new_value=(new_value or "")[:64] or None,
                    reason=reason[:255],
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Owner AI audit failed")


async def _create_branch(branch: str, sha: str) -> None:
    await _github(
        f"/repos/{_repo()}/git/refs",
        method="POST",
        body={"ref": f"refs/heads/{branch}", "sha": sha},
    )


async def _apply_changes(
    branch: str,
    changes: list[dict[str, Any]],
) -> str:
    ref = await _github(
        f"/repos/{_repo()}/git/ref/heads/{urllib.parse.quote(branch, safe='')}"
    )
    parent_sha = str(ref["object"]["sha"])
    commit = await _github(f"/repos/{_repo()}/git/commits/{parent_sha}")
    base_tree = str(commit["tree"]["sha"])

    tree_items: list[dict[str, Any]] = []
    for item in changes:
        path = _safe_repo_path(str(item.get("path", "")))
        content = item.get("content")
        if content is None:
            tree_items.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": None,
                }
            )
            continue
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"محتوای فایل {path} خالی است.")
        _validate_generated_file(path, content)
        blob = await _github(
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

    tree = await _github(
        f"/repos/{_repo()}/git/trees",
        method="POST",
        body={"base_tree": base_tree, "tree": tree_items},
    )
    message = "owner-ai: apply requested project change"
    commit_result = await _github(
        f"/repos/{_repo()}/git/commits",
        method="POST",
        body={
            "message": message,
            "tree": str(tree["sha"]),
            "parents": [parent_sha],
        },
    )
    sha = str(commit_result["sha"])
    await _github(
        f"/repos/{_repo()}/git/refs/heads/{urllib.parse.quote(branch, safe='')}",
        method="PATCH",
        body={"sha": sha, "force": False},
    )
    return sha


async def _create_pr(branch: str, summary: str) -> int:
    result = await _github(
        f"/repos/{_repo()}/pulls",
        method="POST",
        body={
            "title": "Owner AI: requested OPEX MONEY change",
            "head": branch,
            "base": "main",
            "body": (
                "Created by OPEX MONEY Owner AI.\n\n"
                + summary[:4000]
                + "\n\nThe change is merged only after CI succeeds."
            ),
        },
    )
    return int(result["number"])


async def _wait_for_ci(
    *,
    branch: str,
    head_sha: str,
    deadline: float,
) -> tuple[str, str]:
    while asyncio.get_running_loop().time() < deadline:
        result = await _github(
            f"/repos/{_repo()}/actions/runs"
            f"?head={urllib.parse.quote(branch, safe='')}&per_page=20"
        )
        runs = [
            run for run in result.get("workflow_runs", [])
            if str(run.get("head_sha") or "") == head_sha
        ]
        if not runs:
            await asyncio.sleep(CI_POLL_SECONDS)
            continue

        active = [run for run in runs if run.get("status") != "completed"]
        if active:
            await asyncio.sleep(CI_POLL_SECONDS)
            continue

        failures = [
            run for run in runs
            if run.get("conclusion")
            not in {"success", "neutral", "skipped"}
        ]
        if failures:
            return "failed", await _ci_logs(int(failures[0]["id"]))
        return "passed", ""

    return "timeout", "CI قبل از deadline تمام نشد."


async def _merge_pr(pr_number: int) -> bool:
    result = await _github(
        f"/repos/{_repo()}/pulls/{pr_number}/merge",
        method="PUT",
        body={
            "merge_method": "squash",
            "commit_title": "owner-ai: apply approved change",
        },
    )
    return bool(result.get("merged"))


def _candidate_models() -> list[str]:
    values = [
        settings.ai_model,
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
    ]
    return list(dict.fromkeys(item for item in values if item))


async def _planner(
    *,
    request: str,
    history: list[dict[str, Any]],
    tree: list[str],
    inspected: dict[str, str],
    tool_result: str = "",
) -> dict[str, Any]:
    client = companion._get_client()
    if client is None:
        raise RuntimeError("Gemini برای Owner AI در دسترس نیست.")

    tree_text = "\n".join(tree[:5000])
    files_text = "\n\n".join(
        f"FILE {path}\n{content}"
        for path, content in inspected.items()
    )[:MAX_TOTAL_CONTEXT]

    prompt = f"""
You are OPEX MONEY Owner AI, an autonomous senior software architect,
backend engineer, QA engineer, DevOps engineer, and game-system engineer.

The requester is the authenticated project owner.
You are operating on the real repository {ROOT_REPO}.

Primary rule:
Do exactly what the owner asks when technically safe.
Do not invent current code. Inspect files before modifying them.
Preserve the existing game economy unless the owner explicitly requests a rule change.
Never expose secrets or environment variable values.

Available actions:
1. "inspect": request exact repository file paths to read.
2. "search": request a GitHub code-search query and continue.
3. "modify": return complete replacement contents for changed files.
4. "status": report CI/Railway state.
5. "answer": answer without changing code.

For "modify":
- Use the smallest correct change.
- Include tests when behavior changes.
- Do not edit .env, credentials, keys, or secret material.
- Do not commit directly to main. The executor handles branch, PR, CI and merge.
- Do not weaken auth, validation, transaction safety, or locking unless explicitly requested.
- Do not claim success until CI is green.
- Return complete file contents.

JSON only:
{{
  "action":"inspect|search|modify|status|answer",
  "message":"what is happening",
  "paths":["app/..."],
  "query":"optional search query",
  "changes":[{{"path":"...","content":"complete content or null for deletion"}}],
  "summary":"for modifications",
  "final_reply":"user-facing answer when action=answer/status"
}}

OWNER REQUEST:
{request}

CONVERSATION HISTORY:
{json.dumps(history[-8:], ensure_ascii=False)}

REPOSITORY MAP:
{tree_text}

INSPECTED FILES:
{files_text}

LATEST TOOL RESULT:
{tool_result[:12000]}
"""

    for model in _candidate_models():
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=24000,
                    response_mime_type="application/json",
                ),
            )
            parsed = json.loads((response.text or "").strip())
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            logger.warning("Owner AI planner failed model=%s error=%s", model, type(exc).__name__)
    raise RuntimeError("Owner AI planner نتوانست برنامه معتبر تولید کند.")


async def run_owner_ai(
    owner_id: int,
    request: str,
    *,
    history: list[dict[str, Any]] | None = None,
    progress: ProgressCallback | None = None,
) -> OwnerAIResult:
    if not is_owner(owner_id):
        return OwnerAIResult("forbidden", "این دستیار فقط برای مالک فعال است.")

    if not request.strip():
        return OwnerAIResult("empty", "دستور مالک خالی است.")

    started = time.monotonic()
    history = history or []
    run_key = f"{owner_id}-{uuid.uuid4().hex[:12]}"
    await _audit(
        owner_id,
        "owner_ai_start",
        rule_key=f"owner_ai.{run_key}",
        reason=request,
        new_value="started",
    )

    async def say(message: str) -> None:
        if progress is not None:
            await progress(message)

    try:
        if not _github_token():
            return OwnerAIResult(
                "credentials_missing",
                "SUPPORT_GITHUB_TOKEN یا GITHUB_TOKEN برای دسترسی پروژه تنظیم نشده است.",
            )

        await say("در حال خواندن وضعیت واقعی repository و معماری پروژه...")
        tree = await _repo_tree("main")
        inspected: dict[str, str] = {}
        tool_result = ""
        changed: set[str] = set()
        branch: str | None = None
        pr_number: int | None = None
        ci_status = None

        for round_no in range(1, MAX_PLAN_ROUNDS + 1):
            if time.monotonic() - started > DEFAULT_TIMEOUT:
                return OwnerAIResult(
                    "timeout",
                    "زمان اجرای Owner AI تمام شد؛ تغییر ناقص وارد main نشد.",
                    branch=branch,
                    pull_request=pr_number,
                    changed_files=tuple(sorted(changed)),
                    ci=ci_status,
                )

            plan = await _planner(
                request=request,
                history=history,
                tree=tree,
                inspected=inspected,
                tool_result=tool_result,
            )
            action = str(plan.get("action") or "answer").lower()
            message = str(plan.get("message") or "در حال ادامه بررسی.")

            await say(message)

            if action == "answer":
                reply = str(plan.get("final_reply") or message)
                await _audit(
                    owner_id,
                    "owner_ai_done",
                    rule_key=f"owner_ai.{run_key}",
                    reason=reply,
                    old_value="started",
                    new_value="answered",
                )
                return OwnerAIResult("answered", reply)

            if action == "status":
                ci_status, ci_url = await _ci_status(branch)
                railway = await _railway_status()
                reply = str(plan.get("final_reply") or "")
                if not reply:
                    reply = f"CI: {ci_status}\n{ci_url}\n\nRailway:\n{railway}"
                return OwnerAIResult(
                    "status",
                    reply,
                    branch=branch,
                    pull_request=pr_number,
                    changed_files=tuple(sorted(changed)),
                    ci=ci_status,
                    railway=railway,
                )

            if action == "search":
                query = str(plan.get("query") or "").strip()
                if not query:
                    tool_result = "search بدون query."
                    continue
                paths = await _search_code(query)
                tool_result = "نتایج جستجو:\n" + "\n".join(paths)
                history.append({"role": "tool", "content": tool_result})
                continue

            if action == "inspect":
                paths = plan.get("paths") or []
                if not isinstance(paths, list):
                    tool_result = "paths باید list باشد."
                    continue
                read_now: list[str] = []
                for raw_path in paths[:8]:
                    try:
                        clean = _safe_repo_path(str(raw_path))
                        content = await _read_file(clean, branch or "main")
                        inspected[clean] = content
                        read_now.append(clean)
                    except Exception as exc:
                        tool_result += f"\n{raw_path}: {type(exc).__name__}: {exc}"
                tool_result = (
                    "فایل‌های خوانده‌شده:\n"
                    + "\n".join(read_now)
                    + (f"\nخطاها:\n{tool_result}" if tool_result else "")
                )
                continue

            if action == "modify":
                changes = plan.get("changes")
                if not isinstance(changes, list) or not changes:
                    return OwnerAIResult("invalid_plan", "Agent تغییر بدون فایل تولید کرد.")

                if len(changes) > 12:
                    return OwnerAIResult("rejected", "تغییر هم‌زمان به بیش از ۱۲ فایل مجاز نیست.")

                for item in changes:
                    path = _safe_repo_path(str(item.get("path") or ""))
                    content = item.get("content")
                    if content is not None:
                        if not isinstance(content, str):
                            raise ValueError(f"محتوای {path} معتبر نیست.")
                        _validate_generated_file(path, content)

                if branch is None:
                    base = await _github(f"/repos/{_repo()}/git/ref/heads/main")
                    base_sha = str(base["object"]["sha"])
                    branch = f"owner-ai/{uuid.uuid4().hex[:10]}"
                    await _create_branch(branch, base_sha)
                    await say("شاخه موقت ساخته شد. تغییرات فقط روی همان شاخه اعمال می‌شوند.")

                head_sha = await _apply_changes(branch, changes)
                changed.update(str(item["path"]) for item in changes)
                if pr_number is None:
                    pr_number = await _create_pr(branch, str(plan.get("summary") or request))

                await say("تغییر اعمال شد. منتظر CI می‌مانم و فقط در صورت سبز شدن merge می‌کنم.")
                ci_status, ci_evidence = await _wait_for_ci(
                    branch=branch,
                    head_sha=head_sha,
                    deadline=time.monotonic() + min(300, DEFAULT_TIMEOUT),
                )

                if ci_status == "passed":
                    if not await _merge_pr(pr_number):
                        return OwnerAIResult(
                            "merge_failed",
                            "CI سبز شد ولی merge خودکار GitHub انجام نشد.",
                            branch=branch,
                            pull_request=pr_number,
                            changed_files=tuple(sorted(changed)),
                            ci=ci_status,
                        )

                    railway = await _railway_status()
                    await _audit(
                        owner_id,
                        "owner_ai_merge",
                        rule_key=f"owner_ai.{run_key}",
                        reason=str(plan.get("summary") or request),
                        old_value="branch",
                        new_value="merged",
                    )
                    return OwnerAIResult(
                        "fixed",
                        (
                            f"{plan.get('summary') or 'تغییر'}\n\n"
                            "CI سبز شد و تغییر وارد main شد.\n"
                            f"فایل‌ها: {', '.join(sorted(changed))}\n"
                            f"PR: #{pr_number}\n"
                            f"Railway: {railway}"
                        ),
                        branch=branch,
                        pull_request=pr_number,
                        changed_files=tuple(sorted(changed)),
                        ci=ci_status,
                        railway=railway,
                    )

                tool_result = (
                    f"CI وضعیت {ci_status} داد.\n"
                    f"مدرک شکست:\n{ci_evidence[-16000:]}"
                )
                history.append({"role": "tool", "content": tool_result})
                continue

            tool_result = f"action ناشناخته: {action}"

        return OwnerAIResult(
            "incomplete",
            "Agent پس از چند مرحله هنوز به نتیجه قابل اثبات نرسید؛ main بدون تغییر باقی ماند.",
            branch=branch,
            pull_request=pr_number,
            changed_files=tuple(sorted(changed)),
            ci=ci_status,
        )
    except Exception as exc:
        logger.exception("Owner AI failed run=%s", run_key)
        await _audit(
            owner_id,
            "owner_ai_error",
            rule_key=f"owner_ai.{run_key}",
            reason=str(exc),
            old_value="started",
            new_value="error",
        )
        return OwnerAIResult(
            "error",
            f"Owner AI با خطای داخلی متوقف شد: {type(exc).__name__}.",
            branch=branch,
            pull_request=pr_number,
            changed_files=tuple(sorted(changed)),
            ci=ci_status,
        )


__all__ = ["OwnerAIResult", "is_owner", "owner_ids", "run_owner_ai"]
