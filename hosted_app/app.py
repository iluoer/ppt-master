"""Single-user hosted wrapper for PPT Master."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
from typing import Any

import gradio as gr

from agent_runtime.llm_client import ChatClient, LLMConfig
from agent_runtime.projects import (
    REPO_ROOT,
    append_chat,
    create_project,
    find_slide,
    import_uploads,
    latest_pptx,
    render_svg_html,
    save_svg_source,
    slide_names,
    write_user_prompt,
)
from agent_runtime.storage import persist_project
from agent_runtime.storage import list_remote_projects, restore_project
from agent_runtime.tool_agent import ToolAgent


def _state_path(state: dict[str, Any] | None) -> Path | None:
    if not state or not state.get("project_path"):
        return None
    path = Path(state["project_path"])
    return path if path.exists() else None


def _preview(project_path: Path | None, slide_name: str | None = None) -> tuple[list[str], str, str, str]:
    names = slide_names(project_path)
    selected = slide_name if slide_name in names else (names[0] if names else "")
    slide = find_slide(project_path, selected) if project_path and selected else None
    source = slide.read_text(encoding="utf-8", errors="replace") if slide else ""
    return names, selected, render_svg_html(slide), source


def _preview_outputs(project_path: Path | None, slide_name: str | None = None):
    names, selected, html, source = _preview(project_path, slide_name)
    return gr.update(choices=names, value=selected), html, source


def _agent_for(project_path: Path) -> ToolAgent:
    client = ChatClient(LLMConfig.from_env())
    return ToolAgent(client=client, repo_root=REPO_ROOT, project_path=project_path)


def _build_task(message: str, *, auto_confirm: bool, fmt: str, uploaded: list[Path], project_path: Path) -> str:
    upload_note = "\n".join(f"- {path}" for path in uploaded) or "(none)"
    auto = (
        "Auto-confirmation is enabled. Treat this as explicit user approval of your "
        "recommended Eight Confirmations and proceed through final PPTX export."
        if auto_confirm
        else "Auto-confirmation is disabled. Stop after presenting the Eight Confirmations."
    )
    return f"""Create or revise a PPT Master deck.

Canvas format: {fmt}
Project path: {project_path}
Uploaded files copied into sources/uploads:
{upload_note}

{auto}

User message:
{message}
"""


def on_submit(
    message: str,
    uploads: list[str] | None,
    state: dict[str, Any] | None,
    history: list[dict[str, str]] | None,
    fmt: str,
    auto_confirm: bool,
):
    history = history or []
    if not message.strip():
        yield history, state or {}, "Please enter a request.", None, gr.update(choices=[], value=None), "", ""
        return

    project_path = _state_path(state)
    if project_path is None:
        info = create_project(message, fmt=fmt)
        project_path = info.project_path
        state = {"project_name": info.project_name, "project_path": str(project_path)}
        write_user_prompt(project_path, message)
    else:
        state = dict(state or {})

    uploaded = import_uploads(project_path, uploads)
    append_chat(project_path, "user", message)
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": "Started hosted PPT Master agent."})
    slide_update, html, source = _preview_outputs(project_path)
    yield history, state, "Project initialized. Starting agent...", latest_pptx(project_path), slide_update, html, source

    task = _build_task(message, auto_confirm=auto_confirm, fmt=fmt, uploaded=uploaded, project_path=project_path)
    logs: list[str] = []
    try:
        for line in _agent_for(project_path).run(task):
            logs.append(line)
            history[-1]["content"] = "\n".join(logs[-12:])
            slide_update, html, source = _preview_outputs(project_path)
            yield history, state, "\n".join(logs), latest_pptx(project_path), slide_update, html, source
    except Exception as exc:  # noqa: BLE001 - surfaced to the private single-user UI.
        logs.append(f"ERROR: {type(exc).__name__}: {exc}")
        logs.append(persist_project(project_path, latest_pptx(project_path)))
        history[-1]["content"] = "\n".join(logs[-12:])
        slide_update, html, source = _preview_outputs(project_path)
        yield history, state, "\n".join(logs), latest_pptx(project_path), slide_update, html, source
        return

    ppt = latest_pptx(project_path)
    persist_msg = persist_project(project_path, ppt)
    append_chat(project_path, "assistant", "\n".join(logs[-20:]))
    slide_update, html, source = _preview_outputs(project_path)
    logs.append(persist_msg)
    history[-1]["content"] = "Done.\n\n" + "\n".join(logs[-8:])
    yield history, state, "\n".join(logs), ppt, slide_update, html, source


def refresh_preview(state: dict[str, Any] | None, slide_name: str | None):
    project_path = _state_path(state)
    return _preview_outputs(project_path, slide_name)


def save_current_svg(state: dict[str, Any] | None, slide_name: str, content: str):
    project_path = _state_path(state)
    if not project_path:
        return "No active project.", None
    slide = find_slide(project_path, slide_name)
    if not slide:
        return "Slide not found.", None
    save_svg_source(slide, content)
    append_chat(project_path, "system", f"Saved manual SVG edit: {slide_name}")
    ppt = latest_pptx(project_path)
    persist_msg = persist_project(project_path, ppt)
    return f"Saved {slide_name}. Use Re-export PPTX to download the updated deck.\n{persist_msg}", ppt


def reexport(state: dict[str, Any] | None):
    project_path = _state_path(state)
    if not project_path:
        return "No active project.", None
    commands = [
        [sys.executable, "skills/ppt-master/scripts/svg_quality_checker.py", str(project_path)],
        [sys.executable, "skills/ppt-master/scripts/total_md_split.py", str(project_path)],
        [sys.executable, "skills/ppt-master/scripts/finalize_svg.py", str(project_path)],
        [sys.executable, "skills/ppt-master/scripts/svg_to_pptx.py", str(project_path)],
    ]
    logs: list[str] = []
    env = None
    for cmd in commands:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, env=env)
        logs.append("$ " + " ".join(cmd))
        logs.append(f"exit_code={proc.returncode}")
        if proc.stdout:
            logs.append(proc.stdout)
        if proc.stderr:
            logs.append("STDERR:\n" + proc.stderr)
        if proc.returncode != 0:
            break
    ppt = latest_pptx(project_path)
    logs.append(persist_project(project_path, ppt))
    return "\n".join(logs), ppt


def refresh_remote_projects():
    names = list_remote_projects()
    value = names[0] if names else None
    return gr.update(choices=names, value=value), f"Found {len(names)} saved project(s)."


def restore_remote_project(project_name: str | None):
    if not project_name:
        return {}, "Choose a saved project first.", None, gr.update(choices=[], value=None), "", ""
    project_path = restore_project(project_name)
    state = {"project_name": project_path.name, "project_path": str(project_path)}
    slide_update, html, source = _preview_outputs(project_path)
    return state, f"Restored {project_path.name}.", latest_pptx(project_path), slide_update, html, source


def _launch_auth() -> tuple[str, str] | None:
    username = os.environ.get("APP_USERNAME", "").strip()
    password = os.environ.get("APP_PASSWORD", "").strip()
    if username and password:
        return username, password
    return None


def _server_port() -> int:
    return int(os.environ.get("PORT", "7860"))


with gr.Blocks(title="PPT Master Agent") as demo:
    gr.Markdown("# PPT Master Hosted Agent\nSingle-user Render + Supabase wrapper for PPT generation and revision.")
    app_state = gr.State({})

    with gr.Tab("Generate / Chat"):
        chatbot = gr.Chatbot(type="messages", height=420)
        prompt = gr.Textbox(label="Request", lines=4, placeholder="例如：做一份 8 页关于新能源车出海策略的商务 PPT")
        uploads = gr.File(label="Optional source files", file_count="multiple", type="filepath")
        with gr.Row():
            fmt = gr.Dropdown(["ppt169", "ppt43"], value="ppt169", label="Canvas")
            auto_confirm = gr.Checkbox(value=True, label="Auto-confirm recommended design choices")
        submit = gr.Button("Generate / Continue", variant="primary")
        logs = gr.Textbox(label="Agent logs", lines=14)
        ppt_file = gr.File(label="Latest PPTX")
        with gr.Accordion("Restore saved project", open=False):
            saved_projects = gr.Dropdown(label="Saved projects", choices=[])
            with gr.Row():
                refresh_saved = gr.Button("List saved projects")
                restore_saved = gr.Button("Restore selected project")

    with gr.Tab("Preview / Edit"):
        with gr.Row():
            slide_select = gr.Dropdown(label="Slide", choices=[])
            refresh = gr.Button("Refresh preview")
        slide_html = gr.HTML()
        svg_source = gr.Textbox(label="SVG source for selected slide", lines=18)
        with gr.Row():
            save_svg = gr.Button("Save SVG source")
            reexport_btn = gr.Button("Re-export PPTX")
        edit_status = gr.Textbox(label="Edit / export status", lines=8)

    submit.click(
        on_submit,
        inputs=[prompt, uploads, app_state, chatbot, fmt, auto_confirm],
        outputs=[chatbot, app_state, logs, ppt_file, slide_select, slide_html, svg_source],
    )
    refresh.click(
        refresh_preview,
        inputs=[app_state, slide_select],
        outputs=[slide_select, slide_html, svg_source],
    )
    slide_select.change(
        refresh_preview,
        inputs=[app_state, slide_select],
        outputs=[slide_select, slide_html, svg_source],
    )
    save_svg.click(
        save_current_svg,
        inputs=[app_state, slide_select, svg_source],
        outputs=[edit_status, ppt_file],
    )
    reexport_btn.click(reexport, inputs=[app_state], outputs=[edit_status, ppt_file])
    refresh_saved.click(refresh_remote_projects, inputs=[], outputs=[saved_projects, logs])
    restore_saved.click(
        restore_remote_project,
        inputs=[saved_projects],
        outputs=[app_state, logs, ppt_file, slide_select, slide_html, svg_source],
    )


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch(server_name="0.0.0.0", server_port=_server_port(), auth=_launch_auth())
