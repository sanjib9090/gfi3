# Candidate Review — Flask prototype

A human-review tool that downloads the two Tally job descriptions, saves the extracted job descriptions as JSON, extracts resume text, and compares evidence against configurable role criteria. It includes the GFI logo and the data from the attached project. This copy includes the 47 supplied resume extracts in `data/resumes.json` and their individual preview links. Previously saved local uploads are not included. It was **not deployed** as part of this update.

## Frontend files

- `public/index.html` contains the page structure.
- `public/assets/style.css` contains the responsive styling.
- `public/assets/app.js` contains readable, commented vanilla JavaScript for buttons, API requests, rendering, and uploads.
- `app.py` contains the Flask backend. The existing scoring and extraction have not been replaced.

Edit these files directly and refresh the browser. There is no minified JavaScript and no frontend build step.

## Supabase setup

Supabase Storage is the only upload storage backend. You do not need Vercel Blob or the Supabase Python package: Flask calls the Supabase Storage REST API using `requests`, which is already in `requirements.txt`. Configure Supabase even when running the app locally.

1. In Supabase Storage, create a **private** bucket named `candidate-review`.
2. Find your project URL and a server-side `sb_secret_...` key in the Supabase dashboard. A legacy service-role JWT also works. The publishable/anon key will not work here.
3. In Vercel, open **Project → Settings → Environment Variables**, add these variables for the environment you are deploying, and redeploy:

```text
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_BUCKET=candidate-review
SUPABASE_KEY=YOUR_SERVER_SECRET_KEY
```

Use `.env.example` as a template. The project URL has the provided URL as a default. The server checks `SUPABASE_SECRET_KEY` first, then `SUPABASE_KEY`, then the legacy `SUPABASE_SERVICE_ROLE_KEY`; remove any stale `SUPABASE_SECRET_KEY` if using `SUPABASE_KEY`. The value must be a secret key, not a publishable key. Replace the secret placeholder in Vercel's settings; never commit a real secret or paste it into chat. Vercel does not automatically read this template. No `STORAGE_BACKEND`, `APP_PASSWORD`, or Blob variables are required; any old ones have no effect. The application has no login.

For local development, set the same variables in your shell (`export NAME=value` on macOS/Linux or `$env:NAME="value"` in PowerShell), then:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`. The `.env.example` file is not automatically loaded. The server saves uploaded files and extracted records under `resumes/` and `records/` in the private bucket, and cached job descriptions under `jds/`. The **View saved resume** button retrieves a file through Flask. **Remove upload** deletes its file and record from Supabase. Supplied candidates retain their individual WorkDrive links.

This copy contains no local uploaded resumes. Switching to Supabase does not copy previous local or Blob uploads automatically; keep a backup if you need them. This archive was not deployed, and Supabase was tested with mocks, not a real account.

**Privacy warning:** There is no app login. Anyone who can reach the deployment URL can view candidate records and uploaded files, and upload or delete resumes through the app. A private bucket does not prevent this. `data/resumes.json` also contains resume text in the project and deployment bundle. Do not use a public deployment or repository for real personal data without permission and suitable access controls. Keep the server secret private; it bypasses bucket policies.

## Job descriptions and scoring configuration

`data/roles.json` contains the two source URLs, role labels, configurable rule weights and text patterns; the job description itself is not embedded in `app.py`. Flask checks the Supabase `jds/` folder first. If a saved description exists, it is reused without a Tally request, even after a cold start. Otherwise Flask fetches Tally once and saves the extracted JSON to Supabase. The page displays the last fetch time, not a new fetch time. The mapped result is also cached for up to five minutes per instance.

If no saved JSON exists and a Tally fetch fails, the app uses the bundled dated `data/jd_lca.json` / `data/jd_design.json` snapshot. When saved JSON exists, Tally is not contacted. To intentionally fetch a new description, remove the corresponding `jds/lca.json` or `jds/design.json` from the private bucket and redeploy (or allow the five-minute in-memory cache to expire). The next load attempts a fetch. It labels the fallback and refuses to score a description that cannot be mapped to enough criteria. Job rules use keyword matching; manually verify source text, evidence and scoring weights. Uploaded resumes are re-evaluated against the loaded criteria when the candidate list loads. The initial screen shows a loading message; after an error or timeout, it offers a refresh button.

## Extraction and limitations

Uploads support PDF with a text layer, DOCX, ODT, TXT, Markdown, RTF, and HTML. Each file is limited to 3.9 MB; PDFs are limited to 50 pages. Image-only/scanned PDFs require OCR, which this prototype does not perform. Old `.doc` files must be converted first. Unreadable/short extraction is flagged; layout, two-column text, unusual headings and embedded graphics can still produce incomplete results.

The parser recognizes common section-heading variants, including colon-terminated and uppercase headings. When a section is missing but a matching line exists, the app labels it **inferred** for human verification. Inferred experience can be a profile claim; it is not verified employment. A missing match is not proof of missing qualifications, and a match is not proof of proficiency. No score or classification is a hiring decision. The linked LCA posting and the assignment differ on years of experience, so verify both rather than applying an automatic cutoff.

## LCA experience estimation

The LCA experience criterion checks for an explicit claim of LCA years and estimates tenure from date ranges in a labeled experience section near LCA-specific job titles, or general job titles with LCA duties inside the same job block. The configured matching patterns and point buckets live in `data/roles.json`. It uses the larger of the claim and estimate; it does not add them together. Overlapping date ranges are merged. Dates without a day are approximated using the first day of the start month and the last day of the end month; year-only dates use January through December. “Present” is interpreted as the day of analysis. This can overestimate tenure or count stale jobs; it cannot establish the period of LCA duties or independence. The detailed score displays the source titles, dates and estimation date so reviewers can verify them. Employment dates elsewhere, including education and unrelated jobs, are not counted. Layouts with reordered columns or unsupported date formats may require manual review.

The original NP DOCX contains `ISO :2006` without standard numbers. A year alone does not identify ISO 14040 or 14044: such references are flagged for human verification and earn zero until the resume or certificate explicitly identifies an applicable standard.

The LCA candidate detail highlights the estimated or claimed LCA experience in years, with the analysis date and highlighted date evidence. This is role tenure, not a verified count of time spent exclusively on LCA. If no reliable relevant duration is detected, it says not identified rather than zero. Open uploaded source files using **View saved resume**. Supplied resumes use **View resume**, which opens the matching individual WorkDrive preview from `data/resume_links.json`. PDF files can open in-browser; other formats such as DOCX generally download.

## Experience highlights for both roles

Candidate details show (1) relevant experience for the selected job and (2) total recorded work/training, including internships and training. Work entries are identified inside a labeled experience section using common job titles, varied date formats and job-block boundaries. A relevant estimate requires relevant title or duties; LCA and UI/UX patterns live in `data/roles.json`. Month-only and year-only dates are approximate, current roles use the server analysis date, and overlapping date ranges are merged. A role duration does not prove the duration or intensity of its relevant duties. When there are only duration statements without dates, the largest relevant/total duration is displayed as a claim and not added to other undated durations. When dated entries exist, undated durations are omitted to avoid unknown overlap. No identifiable evidence is displayed as “Not identified,” not zero. Verify the evidence and original resume; unusual formats can still be missed. UI/UX experience is informational and does not change its score.

If no dated work entry is recognized, the app can also display an explicit years-of-experience claim from the resume as a claim; it is not assumed to be role-relevant.

## Individual source-resume links

`data/resume_links.json` maps each exact supplied filename to its individual WorkDrive preview URL. The Flask API adds that URL to each matching supplied candidate; the button opens that document directly. If the filename does not have a mapping, the app says the link is unavailable instead of opening a different candidate or the folder. Add a verified preview URL under the exact filename in the JSON file for new supplied records. Uploaded resumes continue to use their own saved file, even if a filename matches a supplied resume. WorkDrive links can stop working if sharing settings change. This project includes the 47 supplied resume records; their filenames match the 47 source-link mappings. Source availability depends on WorkDrive sharing settings.
