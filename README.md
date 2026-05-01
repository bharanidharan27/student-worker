# ASU Student Jobs Resume Assistant

A local human-in-the-loop assistant for collecting ASU student jobs, scoring fit, and generating tailored resume materials.

## What It Does

- Accepts a pasted ASU student job description.
- Can bulk collect ASU Workday student job descriptions from your own logged-in browser session.
- Parses the posting into structured fields.
- Scores fit against Bharanidharan's profile using local rule-based logic.
- Recommends a broad resume type and the exact best PDF from `resumes/master/`.
- Saves a markdown fit report locally.
- Stores deduplicated job records in SQLite.

## What It Does Not Do

- Does not bypass ASU SSO.
- Does not ask for or store an ASU password.
- Does not store Duo/MFA secrets.
- Does not bypass ASU SSO or submit applications unless you explicitly run auto-apply with `--submit`.
- Does not commit Workday session cookies.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install
```

If you use the Windows Python launcher, replace `python` with `py` after Python is installed.

## Initialize Storage

```bash
python -m src.storage.db --init
```

This creates `data/jobs.sqlite` with the local job and generated-document tables.

## Generate a Manual Job Report

Paste a job description interactively:

```bash
python -m src.manual_job_report
```

Or read a text file and choose an output path:

```bash
python -m src.manual_job_report --input-file path\to\job.txt --output outputs\reports\report.md
```

The report includes parsed fields, fit score, fit label, reasons, gaps, job family, exact recommended resume, and extracted keywords.

## Bulk Workday Extraction

The Workday jobs page requires ASU SSO. This app does not bypass login, ask for your password, or store credentials. You log in manually once, and Playwright saves local browser session state in `playwright/.auth/asu_workday.json`.

Capture the login session:

```bash
python -m src.auth.login_capture
```

A browser opens. Complete ASU SSO and Duo/MFA yourself, wait for the student jobs page to load, then press Enter in the terminal.

Check whether the saved session still works:

```bash
python -m src.auth.session_check
```

Run a small scrape first:

```bash
python -m src.scraping.workday_scraper --limit 10
```

If Workday behaves differently than expected, run with a visible browser:

```bash
python -m src.scraping.workday_scraper --limit 10 --headed
```

The scraper opens job cards gently, extracts job-description text, scores each job, and saves deduplicated rows to `data/jobs.sqlite`.

If the terminal appears stuck after saving a few jobs, press `Ctrl+C`. Already saved jobs remain in SQLite. Then rerun a smaller visible scrape:

```bash
python -m src.scraping.workday_scraper --limit 6 --headed --max-scrolls 5 --idle-rounds 1
```

Check how many jobs are currently stored:

```bash
python -m src.storage.db --count jobs
```

Print saved jobs in scrape order:

```bash
python -m src.storage.db --list-jobs --limit 10
```

Refresh fit scores and exact resume recommendations for already scraped jobs without scraping again:

```bash
python -m src.matching.fit_scorer --rescore-db
```

## Applying

The apply CLI can work as a local checklist/status tracker or as a guarded Workday auto-apply helper. It never bypasses SSO. You must capture your own login session first.

### Pick a job, no ids required (recommended)

Three no-friction ways to start an application:

**Apply for the top-ranked job in one command:**

```bash
python -m src.apply_cli --auto-apply-next --headed --submit
```

This picks the highest-scoring `Strong Fit` job (`>= 80`) automatically.

**Pick from a numbered menu:**

```bash
python -m src.apply_cli --pick --headed --submit
```

You will see a list like this and only need to type a single digit:

```
Pick a job to auto-apply:

  [1] Office Aide - WP Carey
      fit: 92/100 Strong Fit | location: Tempe campus | posted: 04/30/2026 | status: new
  [2] Sun Devil Card Aide
      fit: 88/100 Strong Fit | location: Tempe campus | posted: 04/29/2026 | status: new
  [3] Marketing Specialist
      fit: 84/100 Strong Fit | location: Tempe campus | posted: 04/28/2026 | status: new

Type the number of the job to apply for, or 'q' to quit.
Your pick: 2
```

**Paste a Workday URL straight from your browser address bar:**

```bash
python -m src.apply_cli --auto-apply-url "https://asu.wd1.myworkdayjobs.com/...JR12345" --headed --submit
```

The tool finds the matching saved job by URL or Workday id and runs auto-apply.

### Status tracking (optional)

Print the ranked apply queue, mostly for inspection:

```bash
python -m src.apply_cli --queue --limit 10
```

Mark a job's status after you handle it:

```bash
python -m src.apply_cli --mark-reviewing 2
python -m src.apply_cli --mark-applied 2
python -m src.apply_cli --mark-skipped 5 --note "Not interested"
```

(These status commands still take a numeric id from `--queue`. They are bookkeeping only and do not start an application.)

Auto-apply fills the fixed Workday sections shown in the current ASU flow: work authorization, ASU enrollment, federal work study, age 18+, Hispanic/Latino disclosure, disability self-identification, and the review signature checkbox. It uses the recommended resume PDF for upload and the resume parser for experience fields.

### Manual handoff on My Experience

Workday's `My Experience` page contains required dropdowns the resume parser cannot supply (for example `Source`, education `Country`, `Degree`, and `Field of Study`). Auto-apply uploads the resume on Quick Apply, lets Workday parse it into `My Experience`, and then **pauses** so you can:

1. Verify pre-filled work experience, education, and skills.
2. Pick the missing dropdowns Workday flags as required.
3. Click `Save and Continue` yourself.

The terminal prints a `[auto-apply] Paused on 'my experience'` message while it waits. As soon as Workday navigates past `My Experience`, the tool resumes and auto-fills `Application Questions`, `Voluntary Disclosures`, `Self Identify`, and the `Review` signature checkbox. With `--submit`, it then clicks the final `Submit`. The pause times out after 15 minutes and marks the job as `reviewing`.

Allow final submit only when Workday does not show required-field blockers:

```bash
python -m src.apply_cli --auto-apply-next --headed --submit
python -m src.apply_cli --pick --headed --submit
```

Auto-apply the ranked queue. By default this filters to `Strong Fit` jobs with score `>= 80`:

```bash
python -m src.apply_cli --auto-apply-queue --limit 3 --headed
python -m src.apply_cli --auto-apply-queue --limit 3 --headed --submit
```

If Workday shows required questions, validation errors, or an unexpected page shape, the automation stops and marks the job as `reviewing` instead of guessing.
When running with `--headed`, the browser is left open on review/failure so you can inspect and fix the page manually.

If Workday shows an existing status like `Applied 04/30/2026, 1:08 PM`, auto-apply marks the local job as `applied` instead of looking for an Apply button.

For normal use, keep `--limit` small until the selectors are confirmed against the current Workday page.

If the scraper reports `0 visible candidate card(s)`, capture a debug dump:

```bash
python -m src.scraping.workday_scraper --limit 10 --headed --max-scrolls 1 --idle-rounds 1 --debug-dump-dir outputs/debug
```

That writes the current page text and screenshot to `outputs/debug/` so the selectors can be adjusted against the actual page state.

## Dashboard

The Streamlit dashboard is intentionally deferred until a later milestone. `app.py` currently points users to the CLI flow.
