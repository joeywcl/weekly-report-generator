# Weekly Report Generator

Generate formatted weekly reports from a simple web form or command-line interface.

## 🌐 Web Interface (RECOMMENDED)

The easiest way to fill out your weekly report!

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the web server:**
   ```bash
   python app.py
   ```

3. **Open your browser** and go to: `http://127.0.0.1:5000`

4. **Fill out the form** - The web interface provides:
    - Clean, modern UI
    - Easy add/remove buttons for lists
    - Auto-saved drafts in your browser (localStorage)
    - One-click Word document generation and download

5. **Click "Generate Word Document"** - Your formatted report will download automatically!

### ✨ AI Assist (optional)

Transform rough notes into structured reports with AI assistance.

**Setup:** Requires an OpenAI API key: `export OPENAI_API_KEY=sk-...`

**How to use:**

1. **Expand "AI Assist"** at the top of the form
2. **Choose your report style:**
   - **Quick Structure** (default) - Fast extraction, concise bullets. Best for internal updates and quick reports.
   - **Full Report** - Polished narrative with professional context. Best for management reviews, detailed updates, or when you want publication-ready formatting (like ChatGPT output).
3. **Paste your rough notes** (bullets, Jira updates, meeting notes)
4. **Click "Suggest for report"** - AI generates structured content
5. **Review the formatted preview** - Toggle between formatted/JSON view
6. **Click "Apply to form"** - Fields auto-fill with AI suggestions
7. **Edit as needed** and generate your Word document

**Example notes format:**
```
This week:
- Integrated AI insights display
- Created knowledge base management page

AI Acceleration:
- Cursor with Figma MCP: Designed KB page (~1 day)
  - Insight: Worked great with Composer
  - Limitation: Other agents less reliable

Next week:
- Integrate action handling for suggestions
```

**Additional features:**
- **Improve text:** Rewrite or polish specific fields with AI
- **Same notes work for both styles** - Choose Quick or Full Report based on your audience

### Draft persistence

- By default, drafts are saved locally in your browser (per user / per device).
- Optional: set `WEEKLY_REPORT_PERSIST_YAML=1` to also persist the last submitted form to a YAML file on the server (not recommended for multi-user deployments).

### Formatting rules

- Empty fields render as `N/A` in the generated Word document.
- Bullets only render when they are on their own line starting with `- ` (avoid inline bullets like `Sentence. - Bullet`).

## 💻 Command-Line Interface

If you prefer the command line:

1. **Run the interactive form:**
   ```bash
   python fill_weekly_report.py
   ```

2. **Fill out the prompts** - The script will guide you through each section

3. **Your Word document will be generated automatically** as `Weekly_Report_This_Week.docx`

## 📝 Manual YAML Editing

If you prefer to edit the YAML file directly:

1. Edit your input YAML file (see **Multiple users** below if you use a per-user file).
2. Run:
   ```bash
   python generate_weekly_report_from_template.py \
     --template "Weekly_Report_Template.docx" \
     --input "weekly_report_input_template.yaml" \
     --output "Weekly_Report_This_Week.docx"
   ```
   Use your file path for `--input` if you set `WEEKLY_REPORT_INPUT_FILE`.

## 👥 Multiple users

The template is generic: anyone can use the same repo. To avoid overwriting each other’s data, each person can use their own input file:

1. **Copy the template** (optional):  
   `cp weekly_report_input_template.yaml weekly_report_YourName.yaml`
2. **Set your file in `.env`:**
   ```bash
   WEEKLY_REPORT_INPUT_FILE=weekly_report_YourName.yaml
   ```
3. The web app and `fill_weekly_report.py` will then load and save that file.  
   When running the generator by hand, pass your file with `--input`.

The default `weekly_report_input_template.yaml` is a blank template; with the env var unset, everyone shares that one file.

## 📁 Files

- `app.py` - **Web interface (Flask app)** ⭐ RECOMMENDED
- `fill_weekly_report.py` - Command-line interactive form
- `generate_weekly_report_from_template.py` - Core generation script
- `weekly_report_input_template.yaml` - Default input data file (or set `WEEKLY_REPORT_INPUT_FILE` in `.env` for a per-user file)
- `Weekly_Report_Template.docx` - Word template with formatting
- `Weekly_Report_This_Week.docx` - Generated output (created by CLI/manual generation)

## ✨ Features

- **Web interface** with modern, user-friendly design
- **AI-powered report generation** with two styles (Quick Structure / Full Report)
- **Formatted preview** of AI suggestions before applying
- **Saves drafts locally in your browser** (localStorage)
- **Auto-calculates week range** (Monday-Friday of current week)
- **Dynamic lists** - Add/remove items easily
- **Automatic formatting** - Bold, bullets, etc. handled by template
- **No manual formatting needed** - Just fill and generate!

## 🚀 Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start web server
python app.py

# Open http://127.0.0.1:5000 in your browser
```

## 🧰 Production (VM)

📖 **See [DEPLOYMENT.md](DEPLOYMENT.md)** for Docker, free hosting options, and auto-deploy workflows.

- Run behind a WSGI server (recommended: gunicorn):
  ```bash
  pip install -r requirements.txt
  export HOST=0.0.0.0
  export PORT=5000
  gunicorn -w 2 -b 0.0.0.0:${PORT} app:app
  ```
- For multi-user deployments, keep `WEEKLY_REPORT_PERSIST_YAML` **off** (default) so the server stays stateless; drafts live in each user’s browser.
