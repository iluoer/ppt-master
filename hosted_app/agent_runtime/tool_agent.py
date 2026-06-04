"""A constrained tool-loop agent for the hosted app."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, Iterator

from .llm_client import ChatClient


SYSTEM_PROMPT = """You are the hosted PPT Master agent.

You are operating inside the ppt-master repository. Follow the repository
workflow in skills/ppt-master/SKILL.md. The web user has requested hosted
single-user generation and may explicitly enable auto-confirmation; when
auto-confirmation is enabled, treat it as the user's up-front approval of your
recommended Eight Confirmations and continue through export.

Use only the JSON tool protocol below. Reply with exactly one JSON object:

{"action":"read_file","path":"relative/path.md"}
{"action":"list_files","path":"relative/folder","glob":"*.svg"}
{"action":"write_file","path":"absolute/or/relative/project/file","content":"..."}
{"action":"append_file","path":"absolute/or/relative/project/file","content":"..."}
{"action":"run_command","command":["python","skills/ppt-master/scripts/svg_quality_checker.py","<project_path>"]}
{"action":"finish","message":"short completion message"}

Rules:
- First read skills/ppt-master/SKILL.md, then the specific references you need.
- Write only inside PROJECT_PATH. Never edit repository source files.
- Generate SVG pages sequentially. Do not generate pages with a script.
- Re-read PROJECT_PATH/spec_lock.md before writing or revising each SVG page.
- Run the required export sequence one command at a time:
  total_md_split.py, finalize_svg.py, svg_to_pptx.py.
- If image generation is needed, create images/image_prompts.json and run
  image_gen.py --manifest, then image_gen.py --render-md. If it fails, continue
  with placeholders or web/free assets and report it.
- For a revision request, update only the affected page/spec when possible,
  then re-run quality check and export.
- If a command fails, inspect the output, fix the cause, and retry once.
"""


class ToolAgent:
    def __init__(self, *, client: ChatClient, repo_root: Path, project_path: Path) -> None:
        self.client = client
        self.repo_root = repo_root.resolve()
        self.project_path = project_path.resolve()
        self.read_limit = int(os.environ.get("TOOL_READ_MAX_CHARS", "32000"))
        self.command_timeout = int(os.environ.get("TOOL_COMMAND_TIMEOUT", "900"))

    def run(self, task: str, *, max_steps: int | None = None) -> Iterator[str]:
        max_steps = max_steps or int(os.environ.get("AGENT_MAX_STEPS", "120"))
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"REPO_ROOT={self.repo_root}\n"
                    f"PROJECT_PATH={self.project_path}\n\n"
                    f"USER_TASK:\n{task}"
                ),
            },
        ]
        for step in range(1, max_steps + 1):
            raw = self.client.chat(messages)
            action = self._parse_action(raw)
            label = action.get("action", "unknown")
            yield f"[agent step {step}] {label}"
            result = self._execute(action)
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"TOOL_RESULT:\n{result}"})
            if label == "finish":
                yield result
                return
        yield f"Stopped after AGENT_MAX_STEPS={max_steps}. Increase the limit or continue with another message."

    def _parse_action(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = max(parts, key=len).strip()
            if text.startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise RuntimeError(f"Agent did not return JSON: {raw[:1000]}")
        return json.loads(text[start : end + 1])

    def _resolve_read_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.repo_root / path
        path = path.resolve()
        if not self._inside(path, self.repo_root) and not self._inside(path, self.project_path):
            raise RuntimeError(f"read path outside repo/project: {path}")
        return path

    def _resolve_write_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.repo_root / path
        path = path.resolve()
        if not self._inside(path, self.project_path):
            raise RuntimeError(f"write path outside PROJECT_PATH: {path}")
        return path

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _execute(self, action: dict[str, Any]) -> str:
        name = action.get("action")
        if name == "read_file":
            return self._read_file(str(action.get("path", "")))
        if name == "list_files":
            return self._list_files(str(action.get("path", "")), str(action.get("glob", "*")))
        if name == "write_file":
            return self._write_file(str(action.get("path", "")), str(action.get("content", "")), append=False)
        if name == "append_file":
            return self._write_file(str(action.get("path", "")), str(action.get("content", "")), append=True)
        if name == "run_command":
            return self._run_command(action.get("command"))
        if name == "finish":
            return str(action.get("message", "Done."))
        raise RuntimeError(f"unknown action: {name}")

    def _read_file(self, value: str) -> str:
        path = self._resolve_read_path(value)
        if not path.exists() or not path.is_file():
            return f"ERROR: file not found: {path}"
        data = path.read_text(encoding="utf-8", errors="replace")
        if len(data) > self.read_limit:
            return data[: self.read_limit] + f"\n\n[TRUNCATED at {self.read_limit} chars]"
        return data

    def _list_files(self, value: str, pattern: str) -> str:
        root = self._resolve_read_path(value)
        if not root.exists() or not root.is_dir():
            return f"ERROR: directory not found: {root}"
        files = sorted(path.relative_to(self.repo_root) for path in root.glob(pattern) if path.is_file())
        return "\n".join(str(path) for path in files[:300]) or "(no files)"

    def _write_file(self, value: str, content: str, *, append: bool) -> str:
        path = self._resolve_write_path(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as fh:
            fh.write(content)
        return f"wrote {path} ({len(content)} chars, append={append})"

    def _run_command(self, command: Any) -> str:
        argv = self._normalize_command(command)
        self._validate_command(argv)
        env = self._command_env()
        proc = subprocess.run(
            argv,
            cwd=self.repo_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=self.command_timeout,
        )
        output = (proc.stdout or "") + ("\nSTDERR:\n" + proc.stderr if proc.stderr else "")
        if len(output) > self.read_limit:
            output = output[: self.read_limit] + f"\n\n[TRUNCATED at {self.read_limit} chars]"
        return f"exit_code={proc.returncode}\n{output}"

    def _normalize_command(self, command: Any) -> list[str]:
        if isinstance(command, list):
            argv = [str(item) for item in command]
        elif isinstance(command, str):
            argv = shlex.split(command)
        else:
            raise RuntimeError("command must be a list or string")
        if not argv:
            raise RuntimeError("empty command")
        if Path(argv[0]).name in {"python", "python3", "python.exe"}:
            argv[0] = sys.executable
        return argv

    def _validate_command(self, argv: list[str]) -> None:
        exe = Path(argv[0]).name.lower()
        if exe == Path(sys.executable).name.lower():
            if len(argv) < 2:
                raise RuntimeError("python command missing script")
            script = (self.repo_root / argv[1]).resolve() if not Path(argv[1]).is_absolute() else Path(argv[1]).resolve()
            allowed = (self.repo_root / "skills" / "ppt-master" / "scripts").resolve()
            if not self._inside(script, allowed):
                raise RuntimeError(f"python script not allowed: {script}")
            return
        if exe == "rg":
            return
        raise RuntimeError(f"command not allowed: {' '.join(argv)}")

    def _command_env(self) -> dict[str, str]:
        env = os.environ.copy()
        image_key = env.get("IMAGE_OPENAI_API_KEY", "").strip()
        image_base = env.get("IMAGE_OPENAI_BASE_URL", "").strip()
        image_model = env.get("IMAGE_OPENAI_MODEL", "").strip()
        if image_key:
            env["IMAGE_BACKEND"] = "openai"
            env["OPENAI_API_KEY"] = image_key
            if image_base:
                env["OPENAI_BASE_URL"] = image_base
            if image_model:
                env["OPENAI_MODEL"] = image_model
        env.setdefault("PYTHONUTF8", "1")
        return env
