"""User-facing conversation export without task activity or private settings."""

from pathlib import Path
import re


def _visible_assistant_content(content):
    """Match Studio's rendered prose boundary without importing its Qt UI."""
    lines = []
    fenced = False
    for line in content.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"\s*<(?:tool_call\b|function[=>]|parameter[=>])", line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def format_conversation(task):
    """Return a readable Markdown transcript of user and assistant messages."""
    title = str(task.get("title") or "Conversation").replace("\r", " ").replace("\n", " ").strip()
    lines = ["# " + title, ""]
    for message in task.get("messages", []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("display_content", message.get("content"))
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        if role == "assistant" and message.get("source") != "local":
            content = _visible_assistant_content(content)
            if not content:
                continue
        lines.extend(("## " + ("You" if role == "user" else "Assistant"), "", content.rstrip(), ""))
    return "\n".join(lines).rstrip() + "\n"


def save_conversation(task, path):
    """Save a transcript to the path chosen by the user and return that path."""
    destination = Path(path)
    destination.write_text(format_conversation(task), encoding="utf-8")
    return destination
