from pathlib import Path
import os
import sys

# Load .env so OPENAI_API_KEY is available (e.g. for AI assist)
_env_dir = Path(__file__).resolve().parent
_env_file = _env_dir / ".env"
try:
    from dotenv import load_dotenv

    load_dotenv(_env_file)
    load_dotenv()  # also load from current working directory
except ImportError:
    pass
# Fallback: if key still not set (e.g. running without python-dotenv), parse .env manually
if not os.environ.get("OPENAI_API_KEY", "").strip() and _env_file.exists():
    try:
        with open(_env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key, value = key.strip(), value.strip().strip("'\"")
                    if key and key == "OPENAI_API_KEY":
                        os.environ[key] = value
                        break
    except Exception:
        pass

from flask import (
    Flask,
    render_template,
    request,
    send_file,
    jsonify,
    after_this_request,
)
import yaml
import subprocess
import json
import re
from datetime import datetime, timedelta
import tempfile

app = Flask(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    v = str(v).strip().lower()
    if v in ("1", "true", "t", "yes", "y", "on"):
        return True
    if v in ("0", "false", "f", "no", "n", "off", ""):
        return False
    return default


PERSIST_YAML = _env_bool("WEEKLY_REPORT_PERSIST_YAML", default=False)


_INLINE_BULLET_RE = re.compile(r"([.!?:])\s+-\s+")


def _normalize_inline_bullets(text: str) -> str:
    """Convert inline '- bullet' into newline bullets.

    Our doc generator treats only lines starting with '-' as bullets.
    This normalizes patterns like: 'Sentence. - Bullet' -> 'Sentence.\n- Bullet'.
    """
    if not text:
        return text
    s = str(text)
    # Normalize newlines to keep behavior consistent.
    s = s.replace("\r\n", "\n")
    s = _INLINE_BULLET_RE.sub(r"\1\n- ", s)
    return s


def _normalize_report_text_fields(data: dict) -> dict:
    """Normalize bullet formatting in known multiline fields."""
    if not isinstance(data, dict):
        return data

    exe = data.get("execution_output")
    if isinstance(exe, list):
        for item in exe:
            if isinstance(item, dict) and "content" in item:
                item["content"] = _normalize_inline_bullets(item.get("content", ""))

    tl = data.get("transformation_log")
    if isinstance(tl, dict):
        tasks = tl.get("ai_acceleration_tasks")
        if isinstance(tasks, list):
            for t in tasks:
                if isinstance(t, dict) and "insight_failure" in t:
                    t["insight_failure"] = _normalize_inline_bullets(
                        t.get("insight_failure", "")
                    )

    sop = data.get("sop_process_solidification")
    if isinstance(sop, dict):
        items = sop.get("items")
        if isinstance(items, list):
            for x in items:
                if isinstance(x, dict) and "impact" in x:
                    x["impact"] = _normalize_inline_bullets(x.get("impact", ""))

    fr = data.get("friction_blockers_ask")
    if isinstance(fr, list):
        for x in fr:
            if not isinstance(x, dict):
                continue
            x["action_mitigation"] = _normalize_inline_bullets(
                x.get("action_mitigation", "")
            )
            x["ask_attention_needed"] = _normalize_inline_bullets(
                x.get("ask_attention_needed", "")
            )

    return data


def _ensure_suggest_schema(out: dict) -> dict:
    """Ensure /api/suggest output always has the expected keys and types."""
    if not isinstance(out, dict):
        return {
            "weekly_objective": "",
            "execution_output": [],
            "ai_acceleration_tasks": [],
            "next_week_focus": [],
            "friction_blockers_ask": [],
            "sop_items": [],
        }

    out.setdefault("weekly_objective", "")
    out.setdefault("execution_output", [])
    out.setdefault("ai_acceleration_tasks", [])
    out.setdefault("next_week_focus", [])
    out.setdefault("friction_blockers_ask", [])
    out.setdefault("sop_items", [])

    if not isinstance(out.get("weekly_objective"), str):
        out["weekly_objective"] = str(out.get("weekly_objective") or "")

    for k in (
        "execution_output",
        "ai_acceleration_tasks",
        "next_week_focus",
        "friction_blockers_ask",
        "sop_items",
    ):
        if not isinstance(out.get(k), list):
            out[k] = []

    return out


def _normalize_suggest_text_fields(out: dict) -> dict:
    """Normalize bullet formatting for /api/suggest JSON shape."""
    if not isinstance(out, dict):
        return out

    out["weekly_objective"] = _normalize_inline_bullets(out.get("weekly_objective", ""))

    exe = out.get("execution_output")
    if isinstance(exe, list):
        for item in exe:
            if not isinstance(item, dict):
                continue
            if "summary" in item and not isinstance(item.get("summary"), str):
                item["summary"] = str(item.get("summary") or "")
            if "content" in item:
                item["content"] = _normalize_inline_bullets(item.get("content", ""))

    tasks = out.get("ai_acceleration_tasks")
    if isinstance(tasks, list):
        for t in tasks:
            if not isinstance(t, dict):
                continue
            if "insight_failure" in t:
                t["insight_failure"] = _normalize_inline_bullets(
                    t.get("insight_failure", "")
                )

    sop_items = out.get("sop_items")
    if isinstance(sop_items, list):
        for s in sop_items:
            if not isinstance(s, dict):
                continue
            if "impact" in s:
                s["impact"] = _normalize_inline_bullets(s.get("impact", ""))

    fr = out.get("friction_blockers_ask")
    if isinstance(fr, list):
        for f in fr:
            if not isinstance(f, dict):
                continue
            if "action_mitigation" in f:
                f["action_mitigation"] = _normalize_inline_bullets(
                    f.get("action_mitigation", "")
                )
            if "ask_attention_needed" in f:
                f["ask_attention_needed"] = _normalize_inline_bullets(
                    f.get("ask_attention_needed", "")
                )

    return out


_ASK_SECTION_RE = re.compile(r"(?im)^\s*(ask|request|need|attention)\s*:\s*")
_ACTION_SECTION_RE = re.compile(r"(?im)^\s*(action|mitigation)\s*:\s*")


def _notes_has_explicit_ask(notes: str) -> bool:
    return bool(_ASK_SECTION_RE.search(notes or ""))


def _notes_has_explicit_action(notes: str) -> bool:
    return bool(_ACTION_SECTION_RE.search(notes or ""))


def _infer_mild_mitigation_from_friction(friction: str) -> str:
    f = (friction or "").strip().lower()
    if not f:
        return ""
    # Keep this generic: coordination/clarification only.
    if any(w in f for w in ("backend", "api", "payload", "contract", "schema", "data")):
        return "Aligned with backend on the required data contract for the frontend display."
    return "Coordinated with relevant stakeholders to clarify requirements and unblock progress."


def _postprocess_suggest(out: dict, notes: str) -> dict:
    """Apply deterministic rules so Apply-to-form is predictable."""
    if not isinstance(out, dict):
        return out

    has_ask = _notes_has_explicit_ask(notes)
    has_action = _notes_has_explicit_action(notes)

    # Do not invent asks.
    if not has_ask:
        fr_list = out.get("friction_blockers_ask")
        if isinstance(fr_list, list):
            for item in fr_list:
                if isinstance(item, dict):
                    item["ask_attention_needed"] = ""

    # Allow mild mitigation inference if missing.
    fr_list = out.get("friction_blockers_ask")
    if isinstance(fr_list, list):
        for item in fr_list:
            if not isinstance(item, dict):
                continue
            friction = item.get("friction", "")
            action = (item.get("action_mitigation") or "").strip()
            if (not action) and (friction or "").strip():
                # If notes explicitly had an Action section, prefer leaving it blank if model didn't provide.
                # Otherwise, infer a mild mitigation.
                if not has_action:
                    item["action_mitigation"] = _infer_mild_mitigation_from_friction(
                        friction
                    )

    # Ensure AI task insight/limitation is present when AI tasks exist.
    tasks = out.get("ai_acceleration_tasks")
    if isinstance(tasks, list):
        for t in tasks:
            if not isinstance(t, dict):
                continue
            if (t.get("task") or "").strip() and not (
                t.get("insight_failure") or ""
            ).strip():
                t["insight_failure"] = (
                    "- Helpful for scaffolding integration quickly and iterating on wiring\n"
                    "- Still required manual verification against real data/contracts and edge cases"
                )

    # Weekly objective fallback: if empty but work exists, derive from summaries.
    wo = (out.get("weekly_objective") or "").strip()
    if not wo:
        exe = out.get("execution_output")
        if isinstance(exe, list):
            summaries = []
            for x in exe:
                if isinstance(x, dict):
                    s = (x.get("summary") or "").strip()
                    if s:
                        summaries.append(s)
                if len(summaries) >= 2:
                    break
            if summaries:
                if len(summaries) == 1:
                    out["weekly_objective"] = f"Delivered {summaries[0]}."
                else:
                    out["weekly_objective"] = (
                        f"Delivered {summaries[0]} and {summaries[1]}."
                    )

    # Keep objective to a single sentence.
    wo = (out.get("weekly_objective") or "").strip()
    if wo:
        m = re.match(r"^(.+?[.!?])\s+", wo)
        if m:
            out["weekly_objective"] = m.group(1)

    return out


# Optional: set OPENAI_API_KEY in environment for AI assist
def _openai_client(api_key=None):
    key = (api_key or "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return (
            None,
            "No API key. Set OPENAI_API_KEY in environment or provide api_key in request.",
        )
    try:
        from openai import OpenAI

        return OpenAI(api_key=key), None
    except Exception as e:
        return None, str(e)


def get_week_range(date=None):
    """Calculate week range (Monday to Friday) for a given date or today."""
    today = date or datetime.now()
    if isinstance(today, datetime) and today.time() == datetime.min.time():
        pass
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    friday = monday + timedelta(days=4)
    return f"{monday.strftime('%Y-%m-%d')} → {friday.strftime('%Y-%m-%d')}"


def _content_from_legacy_execution(item):
    """Build single content string from old format (description, bullets, order)."""
    description = item.get("description", "").strip()
    bullets = item.get("bullets", []) or []
    if isinstance(bullets, str):
        bullets = [b.strip() for b in bullets.splitlines() if b.strip()]
    order = item.get("order", "bullets_first")
    parts = []
    if order == "description_first":
        if description:
            parts.append(description)
        for b in bullets:
            parts.append("- " + b)
    else:
        for b in bullets:
            parts.append("- " + b)
        if description:
            parts.append(description)
    return "\n".join(parts)


def _normalize_execution_output(raw):
    """Normalize execution_output to list of {summary, content}. Content: lines with '-' at start = bullet."""
    if not raw:
        return [{"summary": "", "content": ""}]
    out = []
    for item in raw:
        if isinstance(item, dict):
            if "content" in item:
                content = item.get("content", "")
            else:
                content = _content_from_legacy_execution(item)
            out.append({"summary": item.get("summary", ""), "content": content})
        else:
            out.append({"summary": "", "content": str(item) if item else ""})
    return out if out else [{"summary": "", "content": ""}]


def _parse_execution_form(form):
    """Parse execution_summary[], execution_content[] into list of dicts."""
    summaries = form.getlist("execution_summary[]")
    contents = form.getlist("execution_content[]")
    n = max(len(summaries), len(contents), 1)
    out = []
    for i in range(n):
        summary = (summaries[i] if i < len(summaries) else "").strip()
        content = (contents[i] if i < len(contents) else "").strip()
        if summary or content:
            out.append({"summary": summary, "content": content})
    return out if out else [{"summary": "", "content": ""}]


def _normalize_sop_items(raw):
    """Normalize sop_process_solidification to list of {item, impact}."""
    if not raw:
        return [{"item": "None", "impact": "N/A"}]
    items = raw.get("items", [])
    if items:
        out = []
        for x in items:
            if isinstance(x, dict):
                out.append(
                    {"item": x.get("item", "None"), "impact": x.get("impact", "N/A")}
                )
            else:
                out.append({"item": "None", "impact": "N/A"})
        return out if out else [{"item": "None", "impact": "N/A"}]
    # Legacy: single item/impact
    return [{"item": raw.get("item", "None"), "impact": raw.get("impact", "N/A")}]


def _parse_sop_form(form):
    """Parse sop_item[], sop_impact[] into list of {item, impact}."""
    items = form.getlist("sop_item[]")
    impacts = form.getlist("sop_impact[]")
    n = max(len(items), len(impacts), 1)
    out = []
    for i in range(n):
        item = (items[i] if i < len(items) else "").strip() or "None"
        impact = (impacts[i] if i < len(impacts) else "").strip() or "N/A"
        out.append({"item": item, "impact": impact})
    return out if out else [{"item": "None", "impact": "N/A"}]


def _parse_friction_form(form):
    """Parse friction form fields (friction_friction[], friction_action_mitigation[], friction_ask_attention[]) into list of dicts."""
    frictions = form.getlist("friction_friction[]")
    actions = form.getlist("friction_action_mitigation[]")
    asks = form.getlist("friction_ask_attention[]")
    n = max(len(frictions), len(actions), len(asks), 1)
    out = []
    for i in range(n):
        friction = (frictions[i] if i < len(frictions) else "").strip()
        action = (actions[i] if i < len(actions) else "").strip()
        ask = (asks[i] if i < len(asks) else "").strip()
        if friction or action or ask:
            out.append(
                {
                    "friction": friction,
                    "action_mitigation": action,
                    "ask_attention_needed": ask,
                }
            )
    return (
        out
        if out
        else [{"friction": "", "action_mitigation": "", "ask_attention_needed": ""}]
    )


def _normalize_friction_items(raw):
    """Normalize friction_blockers_ask to list of {friction, action_mitigation, ask_attention_needed}."""
    if not raw:
        return [{"friction": "", "action_mitigation": "", "ask_attention_needed": ""}]
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append(
                {
                    "friction": item.get("friction", ""),
                    "action_mitigation": item.get("action_mitigation", ""),
                    "ask_attention_needed": item.get("ask_attention_needed", ""),
                }
            )
        else:
            out.append(
                {
                    "friction": str(item) if item else "",
                    "action_mitigation": "",
                    "ask_attention_needed": "",
                }
            )
    return (
        out
        if out
        else [{"friction": "", "action_mitigation": "", "ask_attention_needed": ""}]
    )


def get_input_yaml_path():
    """Input YAML path: WEEKLY_REPORT_INPUT_FILE in .env, or default template (per-user friendly)."""
    path = os.environ.get("WEEKLY_REPORT_INPUT_FILE", "").strip()
    return Path(path) if path else Path("weekly_report_input_template.yaml")


def load_previous_data():
    """Load previous YAML data if it exists"""
    if not PERSIST_YAML:
        return {}
    yaml_file = get_input_yaml_path()
    if yaml_file.exists():
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except:
            pass
    return {}


@app.route("/")
def index():
    """Render the main form"""
    prev_data = load_previous_data()
    default_week = get_week_range()

    prev_week = (prev_data.get("week") or "").strip()
    week_value = prev_week if prev_week else default_week

    # Prepare default values
    defaults = {
        "name": prev_data.get("name", ""),
        "role": prev_data.get("role", ""),
        "name_for_file": prev_data.get("name_for_file", prev_data.get("name", "")),
        "week": week_value,
        "weekly_objective": prev_data.get("weekly_objective", ""),
        "execution_output": _normalize_execution_output(
            prev_data.get("execution_output", [])
        ),
        "ai_tasks": prev_data.get("transformation_log", {}).get(
            "ai_acceleration_tasks", [{}]
        ),
        "sop_items": _normalize_sop_items(
            prev_data.get("sop_process_solidification", {})
        ),
        "friction_blockers_ask": _normalize_friction_items(
            prev_data.get("friction_blockers_ask", [])
        ),
        "next_week_focus": prev_data.get("next_week_focus", [""]),
    }

    # Ensure lists have at least one empty item
    if not defaults["ai_tasks"]:
        defaults["ai_tasks"] = [
            {"task": "", "tool_agent": "", "time_saved": "", "insight_failure": ""}
        ]
    if not defaults["friction_blockers_ask"]:
        defaults["friction_blockers_ask"] = [""]
    if not defaults["next_week_focus"]:
        defaults["next_week_focus"] = [""]
    if not defaults["sop_items"]:
        defaults["sop_items"] = [{"item": "None", "impact": "N/A"}]

    return render_template("index.html", defaults=defaults)


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/generate", methods=["POST"])
def generate():
    """Generate the Word document from form data"""
    try:
        cleanup_paths = []

        @after_this_request
        def _cleanup(response):
            for p in cleanup_paths:
                if not p:
                    continue
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
            return response

        # Parse form data
        data = {
            "name": request.form.get("name", "").strip(),
            "role": request.form.get("role", "").strip(),
            "name_for_file": request.form.get("name_for_file", "").strip() or None,
            "week": request.form.get("week", "").strip(),
            "weekly_objective": request.form.get("weekly_objective", "").strip(),
            "execution_output": _parse_execution_form(request.form),
            "transformation_log": {"ai_acceleration_tasks": []},
            "sop_process_solidification": {"items": _parse_sop_form(request.form)},
            "friction_blockers_ask": _parse_friction_form(request.form),
            "next_week_focus": [
                item.strip()
                for item in request.form.getlist("next_week_focus[]")
                if item.strip()
            ],
        }

        # Parse AI tasks - find all unique task indices
        task_indices = set()
        for key in request.form.keys():
            if key.startswith("ai_task_") and key.endswith("_task"):
                # Extract index from key like "ai_task_0_task"
                idx = key.replace("ai_task_", "").replace("_task", "")
                try:
                    task_indices.add(int(idx))
                except:
                    pass

        for i in sorted(task_indices):
            task = request.form.get(f"ai_task_{i}_task", "").strip()
            tool_agent = request.form.get(f"ai_task_{i}_tool_agent", "").strip()
            time_saved = request.form.get(f"ai_task_{i}_time_saved", "").strip()
            insight_failure = request.form.get(
                f"ai_task_{i}_insight_failure", ""
            ).strip()

            if task or tool_agent or time_saved or insight_failure:
                data["transformation_log"]["ai_acceleration_tasks"].append(
                    {
                        "task": task,
                        "tool_agent": tool_agent,
                        "time_saved": time_saved,
                        "insight_failure": insight_failure,
                    }
                )

        # Validate required fields
        if not data["name"] or not data["role"] or not data["week"]:
            return jsonify({"error": "Please fill in Name, Role, and Week fields"}), 400

        # Use name for file (persist for next time)
        data["name_for_file"] = data.get("name_for_file") or data["name"]

        # Normalize bullet formatting so the Word template renders bullets correctly.
        data = _normalize_report_text_fields(data)

        # Generate Word document (stateless by default for multi-user safety)
        template_file = Path("Weekly_Report_Template.docx")
        if not template_file.exists():
            return jsonify({"error": f"Template file not found: {template_file}"}), 400

        # Optional local convenience: persist the last-submitted form to YAML.
        if PERSIST_YAML:
            yaml_file = get_input_yaml_path()
            with open(yaml_file, "w", encoding="utf-8") as f:
                yaml.dump(
                    data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )

        # Always generate via per-request temp files to avoid cross-user races.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as tf:
            yaml_tmp_path = tf.name
            yaml.dump(
                data,
                tf,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        cleanup_paths.append(yaml_tmp_path)

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".docx", delete=False) as of:
            output_tmp_path = of.name
        cleanup_paths.append(output_tmp_path)

        # Run the generation script
        result = subprocess.run(
            [
                sys.executable,
                "generate_weekly_report_from_template.py",
                "--template",
                str(template_file),
                "--input",
                str(yaml_tmp_path),
                "--output",
                str(output_tmp_path),
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return jsonify(
                {"error": f"Error generating document: {result.stderr}"}
            ), 500

        if not Path(output_tmp_path).exists():
            return jsonify({"error": "Document generation failed"}), 500

        # Filename: User-provided name.docx
        name_for_file = (data.get("name_for_file") or data["name"]).strip()
        safe_name = (
            "".join(c for c in name_for_file if c not in r'\/:*?"<>|').strip()
            or "Report"
        )
        download_name = f"{safe_name}.docx"

        # Return the file for download
        return send_file(
            str(output_tmp_path),
            as_attachment=True,
            download_name=download_name,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


SUGGEST_SYSTEM_DETAILED = """You help transform rough weekly notes into a polished, professional engineering report.

Your goal: Generate a publication-ready report with narrative prose, clear context, and professional positioning.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{
  "weekly_objective": "one professional sentence summarizing what was achieved THIS WEEK (outcome-oriented, past tense)",
  "execution_output": [{"summary": "Specific, report-ready header (e.g., 'AI Canvas - Frontend Setup & Core Implementation')", "content": "Professional narrative with context. Explain what was done, why it matters, how it was implemented. Use paragraphs for context and bullets (lines starting with -) for detailed items. Clearly distinguish completed work from prepared/staged work."}],
  "ai_acceleration_tasks": [{"task": "Detailed description of what you used AI for", "tool_agent": "OpenCode / Cursor / Cursor (Composer) with Figma MCP / ...", "time_saved": "~0.5-2d or 1-4h", "insight_failure": "TEXT STRING with 2-3 bullets formatted as lines starting with - followed by space"}],
  "next_week_focus": ["Specific focus with action verb (e.g., 'Integrate action handling for AI suggestions')"],
  "friction_blockers_ask": [{"friction": "Professional description of blocker", "action_mitigation": "What you did to prepare/mitigate. Frame constructively: what's ready, what's waiting on dependencies.", "ask_attention_needed": "Specific request (only if explicitly mentioned in notes)"}],
  "sop_items": [{"item": "SOP item name", "impact": "Professional description of impact with context"}]
}

CRITICAL - Text Formatting Rules:
All text fields (content, insight_failure, impact, action_mitigation, ask_attention_needed) MUST be plain text strings, NOT arrays or lists.

For bullet points, use this EXACT format:
- First bullet text here
- Second bullet text here
- Third bullet text here

NEVER use Python list syntax like ['item1', 'item2']. NEVER use JSON arrays for text content.

Example of CORRECT insight_failure format:
"insight_failure": "- Highly effective in bridging design intent and implementation when using Cursor Composer\n- Other agents were less reliable for this workflow, requiring fallback to Composer for consistent results"

Example of WRONG format (DO NOT DO THIS):
"insight_failure": "['First point', 'Second point']"

WRITING STYLE - CRITICAL:
- Write in professional, engineering-report tone suitable for management review
- Use complete sentences and smooth narrative flow
- For execution_output[].content: Write 2-3 concise sentences that explain WHAT was done and WHY it matters, then add bullets for details
- Keep paragraphs tight: aim for 2-3 sentences maximum per paragraph (roughly 40-60 words)
- Add professional context: "Integrated X to enable Y" not just "Integrated X"
- Position work strategically: clearly separate "completed and integrated" from "frontend prepared, awaiting backend" or "UI established, pending data contracts"
- Frame blockers constructively: emphasize what IS ready and what you've prepared, then note what's waiting on dependencies
- Make the report immediately publishable without further editing
- Be concise but comprehensive: avoid redundant explanations or over-elaboration

TEXT FORMATTING (CRITICAL):
- All text fields are STRINGS, not arrays or lists
- For bullets, use actual line breaks with "- " prefix (newline character: \n)
- Example: "First paragraph.\n- Bullet one\n- Bullet two\nClosing paragraph."
- NEVER return Python list syntax like ['item1', 'item2']
- NEVER return JSON arrays in place of text strings

CONTENT DEPTH:
- weekly_objective: 12-20 words. Outcome-oriented (what was enabled/unblocked), not just ticket titles
- execution_output: 2-5 items when notes cover multiple themes. Each item should have:
  * summary: Specific header like "AI Canvas - AI Insights Display" or "Knowledge Base Management Page"  
  * content: 1-2 concise paragraphs (2-3 sentences each, ~40-60 words) + 2-4 bullets for key details. Focus on WHAT was done and WHY. Use bullets for implementation specifics, features, or caveats.
- ai_acceleration_tasks: Keep task descriptions concise. For workflows like "Figma → Cursor MCP → code", explain in 1-2 sentences
- Preserve key details from notes: node types, API vs mock, timeline caveats, specific tools used
- Avoid repetition: if the summary says "Knowledge Base Page", don't repeat "knowledge base page" 3 times in content

TIME CLASSIFICATION:
- weekly_objective and execution_output: ONLY past/current week work (what was done or achieved)
- next_week_focus: ONLY future plans explicitly marked as "next week", "upcoming", "will", "to do", "plan"
- If timing is ambiguous, assume it belongs to THIS WEEK

FORMATTING:
- Paragraphs: normal sentences (no leading markers)
- Bullets: Start line with "- " for bullet points
- Never write inline bullets like "Sentence. - Bullet". Always: "Sentence.\n- Bullet"

ASKS & INSIGHTS:
- Do NOT invent asks. Only include ask_attention_needed if notes explicitly state a request
- AI tasks: Always provide meaningful insight_failure (2-3 bullets) showing what worked and limitations
- For action_mitigation: If notes don't provide explicit action, infer mild coordination (aligned with backend, coordinated with team, etc.)

CORRECT insight_failure examples (use these as templates):
Example 1: "- Highly effective in bridging design intent and implementation when using Cursor Composer\n- Other agents were less reliable for this workflow, requiring fallback to Composer for consistent results"
Example 2: "- Helpful for accelerating UI integration and layout iteration\n- Manual validation still required to ensure insight presentation aligns with backend data semantics"
Example 3: "- Effective for quickly scaffolding API usage patterns\n- Backend dependency remains for file preview and download via S3 endpoints"

CORRECT execution_output[].content example (concise but professional):
"Developed a knowledge base management page enabling users to view, upload, and manage documents. This feature centralizes document handling and improves operational efficiency.
- Implemented drag-and-drop file upload interface
- Added file deletion capability
- Prepared preview/download UI, pending S3 backend endpoints"

Keep all arrays (execution_output, ai_acceleration_tasks, next_week_focus, friction_blockers_ask, sop_items) as proper arrays; use empty [] if nothing from notes."""

SUGGEST_SYSTEM = """You help fill a weekly report from rough notes or Jira-style updates.
Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{
  "weekly_objective": "one sentence summary of what was done or achieved THIS WEEK (past/current only)",
  "execution_output": [{"summary": "bold one-liner", "content": "text and bullets. Use a line starting with - for each bullet."}],
  "ai_acceleration_tasks": [{"task": "what you used AI for", "tool_agent": "OpenCode / Cursor / ...", "time_saved": "~0.5-2d or 1-4h", "insight_failure": "insight/limitation text. Use lines starting with - for bullets."}],
  "next_week_focus": ["focus 1", "focus 2"],
  "friction_blockers_ask": [{"friction": "what blocked", "action_mitigation": "what you did", "ask_attention_needed": "what you need"}],
  "sop_items": [{"item": "SOP item name", "impact": "impact text. Use - for bullets."}]
}

CRITICAL:
- weekly_objective = ONLY past/current week work (what was done or achieved). If the notes only mention future plans or "next week", leave weekly_objective as "" (empty string). Do NOT put next week's plans in weekly_objective.
- next_week_focus = future plans (what will be done next week). Put items like "Frontend refactoring" here when notes say "next week" or similar.

- weekly_objective MUST be exactly one short sentence (aim ~10-18 words). Outcome-oriented, no next-week content.

FORMATTING + QUALITY BAR:
- execution_output should be 2-5 items when the notes cover multiple themes/sections (e.g., "Design" + "Implementation" + "Deployment", or separate workstreams like "Facilities"/"Knowledge"/"Model hub"). Do NOT collapse everything into 1 generic item unless the notes truly describe only one small change.
- execution_output[].summary should be a specific, report-ready header (e.g., "AI Canvas - Frontend Setup & Core Implementation"), NOT a vague statement like "Work completed".
- execution_output[].content can be mixed paragraphs and bullets. Use plain lines for paragraphs; use "- " for bullets. If the notes include an inventory/list (node types, features), preserve the key details (API vs mock, notable caveats like delays).
- Prefer pulling concrete nouns and scope from the notes (node names, integrations, links) instead of rewriting as generic statements.
- Keep weekly_objective outcome-oriented (what was enabled/unblocked), not just a ticket title (avoid "X was completed").
- ai_acceleration_tasks: include only if notes mention using AI/tools. Each item should include (task, tool_agent, time_saved, insight_failure). If time_saved is unknown, estimate conservatively or use "".

TIME CLASSIFICATION (avoid misplacing content):
- Do NOT put items into next_week_focus unless the notes explicitly indicate future tense (e.g., "next week", "upcoming", "plan", "will", "to do").
- If timing is ambiguous or unlabeled, assume it belongs to THIS WEEK and place it into execution_output (and weekly_objective if appropriate).
- If the notes only mention AI usage (tool + task) but do not mention next week, do not invent next-week focus.

Keep execution_output, ai_acceleration_tasks, next_week_focus, friction_blockers_ask, sop_items as arrays; use empty [] if nothing from the notes.
  Content and impact: use normal text; start a line with "- " for a bullet point."""

# Bullet formatting guardrail: bullets MUST be on their own lines.
SUGGEST_SYSTEM += "\n\nBULLETS: Do NOT write inline bullets like 'Sentence. - Bullet'. Always put bullets on a new line starting with '- '."

# AI tasks: always return a useful Insight/Limitation when an AI task is present.
SUGGEST_SYSTEM += "\n\nAI TASKS: If ai_acceleration_tasks has any items and the notes do not provide an explicit insight/limitation, write 1-2 generic bullets in insight_failure describing what worked and a limitation. Do not invent project-specific facts."

# Asks: do not invent asks.
SUGGEST_SYSTEM += "\n\nASKS: Keep friction_blockers_ask[].ask_attention_needed as an empty string unless the notes explicitly include an Ask/Request/Need section or phrasing."


@app.route("/api/suggest", methods=["POST"])
def api_suggest():
    """Suggest report fields from pasted notes (e.g. Jira, ideas). Requires OPENAI_API_KEY or api_key in body."""
    try:
        data = request.get_json() or {}
        notes = (data.get("notes") or "").strip()
        if not notes:
            return jsonify({"error": "Missing 'notes' in request body."}), 400
        
        # Check style parameter: 'quick' (default) or 'detailed'
        style = (data.get("style") or "quick").strip().lower()
        if style == "detailed":
            system_prompt = SUGGEST_SYSTEM_DETAILED
            user_prompt = "Transform these rough notes into a polished, professional engineering report:\n\n" + notes
        else:
            system_prompt = SUGGEST_SYSTEM
            user_prompt = "Extract and fill the report structure from these notes:\n\n" + notes
        
        client, err = _openai_client(data.get("api_key"))
        if err:
            return jsonify({"error": err}), 400
        response = client.chat.completions.create(
            model=data.get("model", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        raw = (response.choices[0].message.content or "").strip()
        # Strip markdown code block if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        out = json.loads(raw)
        if isinstance(out, dict):
            out = _ensure_suggest_schema(out)
            out = _normalize_suggest_text_fields(out)
            out = _postprocess_suggest(out, notes)
        return jsonify(out)
    except json.JSONDecodeError as e:
        return jsonify({"error": f"Invalid JSON from model: {e}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/improve", methods=["POST"])
def api_improve():
    """Improve or summarize a piece of text. Optional field context. Requires OPENAI_API_KEY or api_key in body."""
    try:
        data = request.get_json() or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Missing 'text' in request body."}), 400
        client, err = _openai_client(data.get("api_key"))
        if err:
            return jsonify({"error": err}), 400
        field = data.get("field", "general")
        prompt = f"Rewrite the following for a weekly report: clearer, professional, concise. Keep bullets (lines starting with -) as-is. Return only the rewritten text, no explanation.\n\n{text}"
        response = client.chat.completions.create(
            model=data.get("model", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        improved = (response.choices[0].message.content or "").strip()
        improved = _normalize_inline_bullets(improved)
        return jsonify({"improved": improved})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Create templates directory if it doesn't exist
    Path("templates").mkdir(exist_ok=True)
    key_set = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    debug = _env_bool("DEBUG", default=False) or _env_bool("FLASK_DEBUG", default=False)
    host = os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = os.environ.get("PORT", "5000").strip() or "5000"
    try:
        port = int(port_raw)
    except ValueError:
        port = 5000

    print(
        "OPENAI_API_KEY:",
        "set" if key_set else "not set (AI Assist will require key in request)",
    )
    print("WEEKLY_REPORT_PERSIST_YAML:", "on" if PERSIST_YAML else "off")
    print(f"Starting server on http://{host}:{port} (debug={'on' if debug else 'off'})")
    app.run(debug=debug, host=host, port=port)
