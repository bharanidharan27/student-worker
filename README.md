# ASU Student Jobs Resume Assistant

A local, human-in-the-loop assistant for collecting ASU student job postings, scoring fit, selecting resumes, and guiding guarded Workday applications.

The project is designed for personal use with your own ASU Workday session. It stores job data locally in SQLite and keeps the final application decision in your hands.

## What It Does

- Parses pasted job descriptions into structured job details.
- Scrapes ASU Workday student job postings from a manually authenticated browser session.
- Scores each job against a private applicant profile and recommends the best resume.
- Reviews eligibility and resume gaps with local rules, plus an optional LLM provider.
- Creates conservative tailored resume copies from extracted resume sources.
- Tracks application status, notes, run history, and generated documents in SQLite.
- Provides both CLI workflows and a local React/FastAPI dashboard.
- Can run guarded auto-apply flows, pausing when Workday needs manual review.

## Safety Boundaries

- Does not bypass ASU SSO, Duo, or MFA.
- Does not ask for or store your ASU password.
- Does not commit Workday cookies or local auth state.
- Does not fabricate unsupported resume claims.
- Does not click final Workday submit unless you explicitly enable submission.
- Stops or pauses when Workday shows required fields, validation errors, or an unexpected page.

Private runtime files are ignored by git, including `.env`, `data/jobs.sqlite`, `data/applicant_profile.yaml`, `outputs/`, `playwright/.auth/`, `resumes/extracted/`, and `resumes/tailored/`.

## Project Layout

- `src/auth/` - Workday login capture and saved-session checks.
- `src/scraping/` - ASU Workday job extraction.
- `src/matching/` - job parsing, fit scoring, keyword extraction, and resume selection.
- `src/eligibility/` - local and optional LLM eligibility review.
- `src/resume_tailoring.py` - conservative resume tailoring from extracted sources.
- `src/apply_cli.py` and `src/apply_automation.py` - queue management and guarded auto-apply.
- `src/api/` - local FastAPI API for the dashboard.
- `frontend/` - Vite React dashboard.
- `tests/` - Python test coverage for parsing, scoring, storage, scraping, API, and apply flows.

## Setup

Create a Python environment and install the Python dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install
```

Create local configuration files:

```powershell
copy .env.example .env
copy data\applicant_profile.example.yaml data\applicant_profile.yaml
python -m src.storage.db --init
```

Edit `data/applicant_profile.yaml` with non-contact facts that are safe to use for matching and eligibility checks, such as degree level, program, availability, work-study status, technologies, experience domains, certifications, portfolio links, and resume keywords.

LLM review is optional. Without an API key, the local rules still run and nuanced cases are marked for review. To enable an LLM provider, fill in the `LLM_*` values in `.env`.

## Dashboard

Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000
```

Start the frontend in a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

The dashboard includes session capture, session checks, scrape runs, job review, eligibility review, resume tailoring, guarded apply controls, and persisted run history. The API accepts only local browser requests.

## Workday Session

Workday requires ASU SSO. Capture your own browser session once:

```powershell
python -m src.auth.login_capture
```

A browser opens. Complete ASU SSO and Duo/MFA yourself, wait for the student jobs page to load, then press Enter in the terminal.

Check whether the saved session still works:

```powershell
python -m src.auth.session_check
```

The saved Playwright auth state is written under `playwright/.auth/` and is ignored by git.

## Scrape Jobs

Start with a small visible scrape:

```powershell
python -m src.scraping.workday_scraper --limit 10 --headed
```

The scraper opens job cards gently, extracts descriptions, scores each job, recommends a resume, and saves deduplicated rows to `data/jobs.sqlite`.

Useful follow-up commands:

```powershell
python -m src.storage.db --count jobs
python -m src.storage.db --list-jobs --limit 10
python -m src.matching.fit_scorer --rescore-db
```

If Workday changes or the scraper reports no visible cards, capture a debug dump:

```powershell
python -m src.scraping.workday_scraper --limit 10 --headed --max-scrolls 1 --idle-rounds 1 --debug-dump-dir outputs/debug
```

If a scrape appears stuck after saving jobs, press `Ctrl+C`. Saved jobs remain in SQLite. Then retry with a smaller visible run:

```powershell
python -m src.scraping.workday_scraper --limit 6 --headed --max-scrolls 5 --idle-rounds 1
```

## Manual Job Reports

Paste a job description interactively:

```powershell
python -m src.manual_job_report
```

Or read from a text file:

```powershell
python -m src.manual_job_report --input-file path\to\job.txt --output outputs\reports\report.md
```

The report includes parsed fields, fit score, fit label, reasons, gaps, job family, recommended resume, extracted keywords, and eligibility notes.

## Eligibility and Resume Tailoring

Manual job reports store an eligibility review immediately. Scraped jobs can be reviewed from the dashboard, either one job at a time or in bulk.

Eligibility review flags blockers and warnings such as undergraduate-only roles, federal work-study constraints, certification requirements, unclear hours, missing required technologies, and experience requirements. Ineligible jobs are hidden from the apply queue unless you manually enable the eligibility override.

The dashboard `Tailor Resume` action creates a tailored copy under `resumes/tailored/<job-id>-<job-title>/`. It only adds missing requirement items supported by `data/applicant_profile.yaml` or another extracted resume source. Unsupported items are listed in `tailoring_notes.md`.

## Apply Queue

Print the ranked apply queue:

```powershell
python -m src.apply_cli --queue --limit 10
```

Show the next best apply packet:

```powershell
python -m src.apply_cli --next
```

Pick from a numbered menu and run guarded auto-apply:

```powershell
python -m src.apply_cli --pick --headed
```

Auto-apply the top-ranked eligible job:

```powershell
python -m src.apply_cli --auto-apply-next --headed
```

Auto-apply a small filtered queue:

```powershell
python -m src.apply_cli --auto-apply-queue --limit 3 --headed
```

Allow final submit only when you are ready and Workday shows no required-field blockers:

```powershell
python -m src.apply_cli --auto-apply-next --headed --submit
```

Status-only bookkeeping commands:

```powershell
python -m src.apply_cli --mark-reviewing 2
python -m src.apply_cli --mark-applied 2
python -m src.apply_cli --mark-skipped 5 --note "Not interested"
python -m src.apply_cli --override-eligibility 7 --note "Manually reviewed"
```

During auto-apply, the tool uploads the recommended resume and fills supported fixed Workday sections. It pauses on `My Experience` so you can verify parsed experience, education, skills, and required dropdowns before continuing. On `Review`, it stops unless `--submit` is provided.

## Testing

Run the Python tests:

```powershell
pytest
```

Run the frontend tests:

```powershell
cd frontend
npm test
```

Build the frontend:

```powershell
cd frontend
npm run build
```
