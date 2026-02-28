"""Git diff tool — view file diffs."""

import asyncio
import re
from typing import AsyncGenerator, ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from vibe.core.types import ToolStreamEvent

_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB cap

_REF_RE = re.compile(r"^[A-Za-z0-9_./@^~-]+$")


class GitDiffArgs(BaseModel):
    ref: str | None = Field(default=None, description="Git ref to diff against (e.g. HEAD~1, main)")
    path: str | None = Field(default=None, description="Limit diff to this path")
    staged: bool = Field(default=False, description="Show staged (cached) changes")


class GitDiffResult(BaseModel):
    output: str
    truncated: bool
    success: bool


class GitDiffConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class GitDiff(BaseTool[GitDiffArgs, GitDiffResult, GitDiffConfig, BaseToolState]):
    description: ClassVar[str] = "Show git diff output for working tree or staged changes."

    async def run(
        self, args: GitDiffArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GitDiffResult, None]:
        cmd = ["git", "diff"]

        if args.staged:
            cmd.append("--cached")

        if args.ref:
            if not _REF_RE.match(args.ref):
                yield GitDiffResult(output="Invalid ref format", truncated=False, success=False)
                return
            cmd.append(args.ref)

        if args.path:
            if ".." in args.path:
                yield GitDiffResult(output="Path traversal not allowed", truncated=False, success=False)
                return
            cmd.extend(["--", args.path])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            yield GitDiffResult(
                output=stderr.decode().strip(), truncated=False, success=False
            )
            return

        raw = stdout.decode(errors="replace")
        truncated = len(raw.encode()) > _MAX_OUTPUT_BYTES
        if truncated:
            raw = raw[: _MAX_OUTPUT_BYTES] + "\n... (truncated at 64 KB)"

        yield GitDiffResult(output=raw.strip(), truncated=truncated, success=True)
