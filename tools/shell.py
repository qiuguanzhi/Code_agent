"""Bounded and timeout-aware local command execution."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from threading import Thread
from typing import Any, TextIO

from tools.filesystem import PathViolation, resolve_in_workspace


DEFAULT_ALLOWED_EXECUTABLES: frozenset[str] = frozenset(
    {
        "git",
        "git.exe",
        "dir",
        "ls",
        "py",
        "py.exe",
        "pytest",
        "pytest.exe",
        "python",
        "python.exe",
        "python3",
        "python3.exe",
    }
)
SENSITIVE_ENV_MARKERS: tuple[str, ...] = (
    "API_KEY",
    "AUTHORIZATION",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


class BoundedHeadTailBuffer:
    """Retain bounded output while preserving both the beginning and the end."""

    def __init__(self, max_chars: int) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self.head_limit = max_chars // 2
        self.tail_limit = max_chars - self.head_limit
        self._head = ""
        self._tail = ""
        self.total_chars = 0

    @property
    def truncated(self) -> bool:
        """Return whether any output is omitted from the rendered value."""

        return self.total_chars > self.max_chars

    def feed(self, text: str) -> None:
        """Append text while retaining only the configured head and tail."""

        if not text:
            return
        self.total_chars += len(text)

        head_remaining = self.head_limit - len(self._head)
        if head_remaining > 0:
            self._head += text[:head_remaining]
            text = text[head_remaining:]

        if text and self.tail_limit > 0:
            self._tail = (self._tail + text)[-self.tail_limit :]

    def render(self) -> str:
        """Render retained output with an explicit omission marker when needed."""

        if not self.truncated:
            return self._head + self._tail
        omitted = self.total_chars - len(self._head) - len(self._tail)
        marker = f"\n... [truncated {omitted} characters] ...\n"
        return self._head + marker + self._tail


def _error(
    code: str,
    message: str,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable command-error payload."""

    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
        "meta": meta or {},
    }


def build_sanitized_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy the process environment while excluding likely credentials."""

    original = dict(os.environ)
    if source is not None:
        original.update(source)
    sanitized: dict[str, str] = {}
    for key, value in original.items():
        normalized_key = key.upper()
        if any(marker in normalized_key for marker in SENSITIVE_ENV_MARKERS):
            continue
        sanitized[key] = value
    sanitized.setdefault("PYTHONIOENCODING", "utf-8")
    return sanitized


def _environment_diagnostic(environment: Mapping[str, str]) -> str:
    """Describe PATH availability without printing its potentially sensitive value."""

    path_value = environment.get("PATH") or environment.get("Path") or ""
    entry_count = len([entry for entry in path_value.split(os.pathsep) if entry])
    return f"path_present={bool(path_value)} path_entries={entry_count}"


def _portable_directory_listing(
    workspace: Path,
    checked_cwd: Path,
    argv: Sequence[str],
    max_output_chars: int,
) -> dict[str, Any]:
    """Implement Windows ``ls``/``dir`` safely without invoking a command shell."""

    started_at = time.monotonic()
    supported_flags = {"-a", "-l", "-al", "-la", "--all", "/a", "/b"}
    targets: list[str] = []
    for argument in argv[1:]:
        if argument.lower() in supported_flags:
            continue
        if argument.startswith("-") or argument.startswith("/"):
            return _error(
                "invalid_command",
                f"unsupported directory-listing option: {argument}",
                meta={"argv": list(argv)},
            )
        targets.append(argument)
    if len(targets) > 1:
        return _error(
            "invalid_command",
            "ls/dir accepts at most one directory path",
            meta={"argv": list(argv)},
        )

    try:
        root = workspace.resolve(strict=True)
        target = (checked_cwd / (targets[0] if targets else ".")).resolve(strict=True)
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return _error(
            "path_violation",
            "directory-listing target must stay inside the workspace",
            meta={"argv": list(argv)},
        )
    if not target.is_dir():
        return _error(
            "invalid_cwd",
            "directory-listing target is not a directory",
            meta={"argv": list(argv)},
        )

    output = BoundedHeadTailBuffer(max_output_chars)
    try:
        for entry in sorted(target.iterdir(), key=lambda item: item.name.casefold()):
            suffix = "/" if entry.is_dir() else ""
            output.feed(f"{entry.name}{suffix}\n")
    except OSError as exc:
        return _error(
            "command_execution_failed",
            f"directory listing failed: {exc}",
            meta={"argv": list(argv)},
        )
    duration_ms = round((time.monotonic() - started_at) * 1_000)
    return {
        "ok": True,
        "data": output.render(),
        "error": None,
        "meta": {
            "argv": list(argv),
            "cwd": str(checked_cwd),
            "exit_code": 0,
            "timed_out": False,
            "cancelled": False,
            "duration_ms": duration_ms,
            "output_chars": output.total_chars,
            "output_truncated": output.truncated,
            "portable_alias": True,
        },
    }


def _validate_argv(
    argv: Sequence[str],
    allowed_executables: Collection[str],
) -> list[str]:
    """Validate command arguments without invoking a shell parser."""

    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError("argv must be a non-empty sequence of strings")
    if len(argv) > 64:
        raise ValueError("argv contains too many elements")

    checked: list[str] = []
    for item in argv:
        if not isinstance(item, str) or not item:
            raise ValueError("every argv element must be a non-empty string")
        if "\x00" in item:
            raise ValueError("argv contains a NUL byte")
        if len(item) > 8_192:
            raise ValueError("an argv element is too long")
        checked.append(item)

    executable_name = Path(checked[0]).name.lower()
    normalized_allowlist = {item.lower() for item in allowed_executables}
    if executable_name not in normalized_allowlist:
        raise ValueError(f"executable is not allowed: {executable_name}")
    return checked


def _drain_stdout(stream: TextIO, buffer: BoundedHeadTailBuffer) -> None:
    """Continuously drain process output to avoid a full-pipe deadlock."""

    while True:
        chunk = stream.read(4_096)
        if not chunk:
            break
        buffer.feed(chunk)


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate a process group on POSIX or a process tree on Windows."""

    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
        if process.poll() is None:
            process.kill()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def run_command(
    workspace: Path,
    argv: Sequence[str],
    cwd: str = ".",
    timeout_seconds: int = 30,
    max_output_chars: int = 20_000,
    *,
    allowed_executables: Collection[str] = DEFAULT_ALLOWED_EXECUTABLES,
    environment: Mapping[str, str] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run one allow-listed executable with ``shell=False`` inside a workspace.

    This limits accidental command expansion and credential inheritance. It is
    not a substitute for a container or operating-system sandbox when running
    untrusted project code.
    """

    if not 1 <= timeout_seconds <= 120:
        return _error("invalid_timeout", "timeout_seconds must be between 1 and 120")
    if not 1_000 <= max_output_chars <= 50_000:
        return _error(
            "invalid_output_limit",
            "max_output_chars must be between 1000 and 50000",
        )

    try:
        checked_argv = _validate_argv(argv, allowed_executables)
    except ValueError as exc:
        return _error("invalid_command", str(exc))

    try:
        checked_cwd = resolve_in_workspace(workspace, cwd, must_exist=True)
    except PathViolation as exc:
        return _error("path_violation", str(exc), meta={"cwd": cwd})
    if not checked_cwd.is_dir():
        return _error("invalid_cwd", "cwd is not a directory", meta={"cwd": cwd})

    sanitized_environment = build_sanitized_env(environment)
    print(
        "[Cerebro::Shell] "
        f"cwd={checked_cwd} argv={checked_argv!r} "
        f"{_environment_diagnostic(sanitized_environment)}"
    )
    executable_name = Path(checked_argv[0]).name.lower()
    if os.name == "nt" and executable_name in {"ls", "dir"}:
        result = _portable_directory_listing(
            workspace,
            checked_cwd,
            checked_argv,
            max_output_chars,
        )
        print(
            "[Cerebro::Shell] "
            f"returncode={result.get('meta', {}).get('exit_code')} "
            f"ok={result.get('ok')} alias={executable_name}"
        )
        return result

    popen_kwargs: dict[str, Any] = {
        "args": checked_argv,
        "cwd": checked_cwd,
        "env": sanitized_environment,
        "shell": False,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    started_at = time.monotonic()
    try:
        process: subprocess.Popen[str] = subprocess.Popen(**popen_kwargs)
    except FileNotFoundError:
        print(
            "[Cerebro::Shell] "
            f"returncode=not_started error=command_not_found cwd={checked_cwd}"
        )
        return _error(
            "command_not_found",
            "executable was not found",
            meta={"argv": checked_argv, "cwd": str(checked_cwd)},
        )
    except OSError as exc:
        print(
            "[Cerebro::Shell] "
            f"returncode=not_started error={type(exc).__name__} cwd={checked_cwd}"
        )
        return _error(
            "command_start_failed",
            str(exc),
            meta={"argv": checked_argv, "cwd": str(checked_cwd)},
        )

    output = BoundedHeadTailBuffer(max_output_chars)
    if process.stdout is None:
        _kill_process_tree(process)
        return _error("stdout_unavailable", "failed to capture process output")

    reader = Thread(target=_drain_stdout, args=(process.stdout, output), daemon=True)
    reader.start()
    timed_out = False
    cancelled = False
    deadline = started_at + timeout_seconds
    while process.poll() is None:
        if should_stop is not None and should_stop():
            cancelled = True
            _kill_process_tree(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_process_tree(process)
            break
        time.sleep(0.05)
    try:
        exit_code = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        exit_code = process.wait(timeout=5)
    finally:
        reader.join(timeout=2)
        if reader.is_alive():
            process.stdout.close()
            reader.join(timeout=1)

    duration_ms = round((time.monotonic() - started_at) * 1_000)
    rendered_output = output.render()
    error_code: str | None
    if cancelled:
        error_code = "cancelled"
    elif timed_out:
        error_code = "timeout"
    elif exit_code != 0:
        error_code = "nonzero_exit"
    else:
        error_code = None

    result = {
        "ok": error_code is None,
        "data": rendered_output,
        "error": (
            None
            if error_code is None
            else {
                "code": error_code,
                "message": (
                    "command cancelled by user"
                    if cancelled
                    else "command exceeded its timeout"
                    if timed_out
                    else f"command exited with code {exit_code}"
                ),
            }
        ),
        "meta": {
            "argv": checked_argv,
            "cwd": cwd,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "duration_ms": duration_ms,
            "output_chars": output.total_chars,
            "output_truncated": output.truncated,
        },
    }
    stderr_preview = rendered_output[-300:].replace("\n", "\\n") if error_code else ""
    print(
        "[Cerebro::Shell] "
        f"returncode={exit_code} ok={error_code is None} "
        f"duration_ms={duration_ms} stderr_tail={stderr_preview!r}"
    )
    return result
