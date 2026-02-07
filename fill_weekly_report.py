#!/usr/bin/env python3
"""
Interactive form to fill out weekly report and generate Word document.
This script guides you through filling out all fields, then generates
the YAML file and Word document automatically.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

# Load .env so WEEKLY_REPORT_INPUT_FILE is available
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
    load_dotenv()
except ImportError:
    pass


def get_input_yaml_path():
    """Input YAML path: WEEKLY_REPORT_INPUT_FILE in .env, or default (per-user friendly)."""
    path = os.environ.get("WEEKLY_REPORT_INPUT_FILE", "").strip()
    return Path(path) if path else Path("weekly_report_input_template.yaml")


def get_week_range():
    """Calculate current week range (Monday to Friday)"""
    today = datetime.now()
    # Get Monday of current week
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)
    friday = monday + timedelta(days=4)
    return f"{monday.strftime('%Y-%m-%d')} → {friday.strftime('%Y-%m-%d')}"


def prompt(question, default=None, required=True):
    """Prompt user for input with optional default value"""
    if default:
        response = input(f"{question} [{default}]: ").strip()
        return response if response else default
    else:
        while True:
            response = input(f"{question}: ").strip()
            if response or not required:
                return response
            print("This field is required. Please enter a value.")


def prompt_list(question, item_label="Item"):
    """Prompt for a list of items"""
    items = []
    print(f"\n{question}")
    print("(Press Enter on an empty line to finish)")
    i = 1
    while True:
        item = input(f"{item_label} {i} (or press Enter to finish): ").strip()
        if not item:
            break
        items.append(item)
        i += 1
    return items


def prompt_ai_task(task_num):
    """Prompt for a single AI acceleration task"""
    print(f"\n--- AI Acceleration Task {task_num} ---")
    task = prompt("Task description", required=True)
    tool_agent = prompt("Tool / Agent used", required=True)
    time_saved = prompt("Time Saved (Est.)", required=True)
    insight_failure = prompt("Insight / Limitation", required=True)
    return {
        "task": task,
        "tool_agent": tool_agent,
        "time_saved": time_saved,
        "insight_failure": insight_failure,
    }


def prompt_ai_tasks():
    """Prompt for multiple AI acceleration tasks"""
    tasks = []
    print("\n" + "=" * 60)
    print("AI ACCELERATION TASKS")
    print("=" * 60)
    print("Enter your AI acceleration tasks. Press Enter when done.")

    task_num = 1
    while True:
        add_more = input(f"\nAdd AI task {task_num}? (y/n): ").strip().lower()
        if add_more != "y":
            break
        tasks.append(prompt_ai_task(task_num))
        task_num += 1

    return tasks


def main():
    print("=" * 60)
    print("WEEKLY REPORT FORM")
    print("=" * 60)
    print("\nFill out the form below. Press Enter to use default values where shown.\n")

    # Load previous values if they exist
    prev_file = get_input_yaml_path()
    prev_data = {}
    if prev_file.exists():
        try:
            with open(prev_file, encoding="utf-8") as f:
                prev_data = yaml.safe_load(f) or {}
        except:
            pass

    # Basic Info
    print("\n" + "-" * 60)
    print("BASIC INFORMATION")
    print("-" * 60)
    name = prompt("Name", prev_data.get("name", ""))
    role = prompt("Role", prev_data.get("role", ""))
    week = prompt("Week", get_week_range())

    # Weekly Objective
    print("\n" + "-" * 60)
    print("WEEKLY OBJECTIVE")
    print("-" * 60)
    weekly_objective = prompt(
        "Weekly Objective (One Sentence)", prev_data.get("weekly_objective", "")
    )

    # Execution & Output
    print("\n" + "-" * 60)
    print("EXECUTION & OUTPUT")
    print("-" * 60)
    execution_output = prompt_list("Enter your execution & output items:")

    # AI Acceleration Tasks
    ai_tasks = prompt_ai_tasks()

    # SOP & Process Solidification
    print("\n" + "-" * 60)
    print("SOP & PROCESS SOLIDIFICATION")
    print("-" * 60)
    sop_item = prompt(
        "Item", prev_data.get("sop_process_solidification", {}).get("item", "None")
    )
    sop_impact = prompt(
        "Impact", prev_data.get("sop_process_solidification", {}).get("impact", "N/A")
    )

    # Friction, Blockers & Ask
    print("\n" + "-" * 60)
    print("FRICTION, BLOCKERS & ASK")
    print("-" * 60)
    friction_blockers_ask = prompt_list("Enter friction, blockers, or asks:")

    # Next Week's Focus
    print("\n" + "-" * 60)
    print("NEXT WEEK'S FOCUS")
    print("-" * 60)
    next_week_focus = prompt_list("Enter next week's focus items:")

    # Build data structure
    data = {
        "name": name,
        "role": role,
        "week": week,
        "weekly_objective": weekly_objective,
        "execution_output": execution_output,
        "transformation_log": {"ai_acceleration_tasks": ai_tasks},
        "sop_process_solidification": {"item": sop_item, "impact": sop_impact},
        "friction_blockers_ask": friction_blockers_ask,
        "next_week_focus": next_week_focus,
    }

    # Save YAML
    yaml_file = get_input_yaml_path()
    with open(yaml_file, "w", encoding="utf-8") as f:
        yaml.dump(
            data, f, default_flow_style=False, allow_unicode=True, sort_keys=False
        )

    print(f"\n✓ Saved YAML to: {yaml_file}")

    # Generate Word document
    print("\nGenerating Word document...")
    template_file = Path("Weekly_Report_Template.docx")
    output_file = Path("Weekly_Report_This_Week.docx")

    if not template_file.exists():
        print(f"❌ Error: Template file not found: {template_file}")
        print("Please make sure Weekly_Report_Template.docx exists in this directory.")
        sys.exit(1)

    try:
        subprocess.run(
            [
                sys.executable,
                "generate_weekly_report_from_template.py",
                "--template",
                str(template_file),
                "--input",
                str(yaml_file),
                "--output",
                str(output_file),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        print(f"✓ Generated Word document: {output_file}")
        print("\n" + "=" * 60)
        print("SUCCESS! Your weekly report is ready.")
        print("=" * 60)

    except subprocess.CalledProcessError as e:
        print("❌ Error generating Word document:")
        print(e.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
