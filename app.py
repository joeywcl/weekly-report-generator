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

        # Filename: [CAP Weekly Report] Name.docx
        name_for_file = (data.get("name_for_file") or data["name"]).strip()
        safe_name = (
            "".join(c for c in name_for_file if c not in r'\/:*?"<>|').strip()
            or "Report"
        )
        download_name = f"[CAP Weekly Report] {safe_name}.docx"

        # Return the file for download
        return send_file(
            str(output_tmp_path),
            as_attachment=True,
            download_name=download_name,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


SUGGEST_SYSTEM = """You help fill a weekly report from rough notes or Jira-style updates.
Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{
  "weekly_objective": "one sentence summary of what was done or achieved THIS WEEK (past/current only)",
  "execution_output": [{"summary": "bold one-liner", "content": "text and bullets. Use a line starting with - for each bullet."}],
  "ai_acceleration_tasks": [{"task": "what you used AI for", "tool_agent": "OpenCode / Cursor / ...", "time_saved": "~0.5-2d or 1-4h", "insight_failure": "insight text. Use lines starting with - for bullets."}],
  "next_week_focus": ["focus 1", "focus 2"],
  "friction_blockers_ask": [{"friction": "what blocked", "action_mitigation": "what you did", "ask_attention_needed": "what you need"}],
  "sop_items": [{"item": "SOP item name", "impact": "impact text. Use - for bullets."}]
}

CRITICAL:
- weekly_objective = ONLY past/current week work (what was done or achieved). If the notes only mention future plans or "next week", leave weekly_objective as "" (empty string). Do NOT put next week's plans in weekly_objective.
- next_week_focus = future plans (what will be done next week). Put items like "Frontend refactoring" here when notes say "next week" or similar.

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


@app.route("/api/suggest", methods=["POST"])
def api_suggest():
    """Suggest report fields from pasted notes (e.g. Jira, ideas). Requires OPENAI_API_KEY or api_key in body."""
    try:
        data = request.get_json() or {}
        notes = (data.get("notes") or "").strip()
        if not notes:
            return jsonify({"error": "Missing 'notes' in request body."}), 400
        client, err = _openai_client(data.get("api_key"))
        if err:
            return jsonify({"error": err}), 400
        response = client.chat.completions.create(
            model=data.get("model", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": SUGGEST_SYSTEM},
                {
                    "role": "user",
                    "content": "Extract and fill the report structure from these notes:\n\n"
                    + notes,
                },
            ],
            temperature=0.3,
        )
        raw = (response.choices[0].message.content or "").strip()
        # Strip markdown code block if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        out = json.loads(raw)
        # Safety net: ensure bullets render even if model returns inline '- bullet'.
        if isinstance(out, dict):
            out = _normalize_report_text_fields(
                {
                    "execution_output": out.get("execution_output"),
                    "transformation_log": {
                        "ai_acceleration_tasks": out.get("ai_acceleration_tasks")
                    },
                    "sop_process_solidification": {"items": out.get("sop_items")},
                    "friction_blockers_ask": out.get("friction_blockers_ask"),
                }
            )
            # Map back to API shape.
            out["execution_output"] = out.get("execution_output")
            out["ai_acceleration_tasks"] = (out.get("transformation_log") or {}).get(
                "ai_acceleration_tasks", []
            )
            out["sop_items"] = (out.get("sop_process_solidification") or {}).get(
                "items", []
            )
            out.pop("transformation_log", None)
            out.pop("sop_process_solidification", None)
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
