# KIT Bot

This version of the bot keeps the original working prototype, but adds real integration paths for Virginia Tech timetable data, RateMyProfessors data, and grade-history data.

The bot can now run in three different ways:

1. **Live timetable + live RMP + local UDC export**
2. **Live timetable + live RMP + captured UDC JSON response**
3. **Mock fallback mode** when a live source is missing

## What was added

- real VT timetable provider through the official VT timetable endpoint
- real RateMyProfessors provider through the public GraphQL route used by community wrappers
- grade-history provider that can ingest either:
  - a CSV export from UDC
  - a JSON payload captured from the browser network tab
  - a direct request URL plus headers/cookies if you want to run it locally
- automatic fallback to the sample catalog if a live source is unavailable
- recommendation enrichment from professor ratings and grade history
- seat alerts tied to the live timetable provider when available

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
```

### 2. Activate it

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
source .venv/bin/activate
```

### 3. Install packages

```bash
pip install -r requirements.txt
```

### 4. Copy the env template

```bash
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
Copy-Item .env.example .env
```

### 5. Fill in `.env`

At minimum, set:

```env
DISCORD_TOKEN=your_bot_token_here
DISCORD_GUILD_ID=your_server_id_here
DATABASE_PATH=data/kit_bot.db
```

Optional source settings:

```env
CATALOG_PROVIDER=auto

RMP_PROVIDER=auto
RMP_SCHOOL_NAME=Virginia Tech

GRADES_PROVIDER=auto
GRADES_CSV_PATH=data/imports/udc_grades.csv
```

## Running the bot

```bash
python bot.py
```

## Recommended first-time flow in Discord

```text
/profile major:CS semester:"current semester"
/myprofile
/uploaddars file:<your_dars.pdf> term:"Fall 2026"
/switchschedule schedule:current
/planschedule description:"easy schedule, average GPA above 3.6, all classes from 9am to 5pm"
/planschedule
/recommend
/recommend description:"avoid Friday and prefer classes after 10am"
/coursegrades course:CS3704
```

`/profile` shows three semester choices from the calendar: current semester, next main semester, and next off semester.

### Option B: Point the bot at a local file in `.env`

```env
GRADES_CSV_PATH=data/imports/udc_grades.csv
```

You can create that CSV directly from the public UDC grade API:

```bash
python scripts/fetch_udc_grades.py --subject CS --output data/imports/udc_grades.csv
```

For a single course while testing:

```bash
python scripts/fetch_udc_grades.py --subject CS --course-number 3704 --output data/imports/udc_grades.csv
```

### Option C: Point the bot at a captured request

If you inspected the UDC network request in DevTools, you can store the request URL plus headers/cookies in `.env`:

```env
GRADES_REQUEST_URL=https://example.com/some/request
GRADES_HEADERS_JSON={"accept":"application/json"}
GRADES_COOKIES_JSON={"sessionid":"..."}
```

That mode is meant to run on your own machine with your own login state.

## DARS import

Users can upload a DARS PDF directly in Discord:

```text
/uploaddars file:<your_dars.pdf> term:"Fall 2026"
```

The bot reads the PDF in memory, extracts only planning fields, and does not save the PDF or personal header data. It updates the profile with detected major/school/term, exact needed course tokens, completed course tokens, current in-progress courses, planned/future courses, remaining credit buckets, requirement hints for recommendations, and the courses listed as in-progress for the selected term.

DARS imports are used only for recommendation context. The bot may read major, term, requirement hints, needed courses, completed courses, unmet credit buckets, current in-progress courses, planned/future courses, and in-progress course codes from the PDF, but it does not create schedule entries from DARS. Use `/addclass` with a CRN or course code to build your actual schedule. Recommendations prioritize exact unmet DARS requirements, use credit buckets such as electives as softer ranking hints, and avoid recommending courses DARS shows as already completed, currently in progress, or already planned.

Both `/recommend` and `/planschedule` use this DARS context. The planner searches exact unmet DARS course codes directly, then fills remaining options with major-relevant courses and electives that can help satisfy remaining credit buckets.

`/planschedule` first tries to satisfy the user's full request. If no exact schedule exists, it returns the best available non-conflicting plans and labels any relaxed preferences, such as a GPA average below the requested floor or a section outside the requested time window.

The text request on `/recommend` and `/planschedule` is optional. If the user leaves it blank, the bot uses the saved/default recommendation behavior. If the user includes text, the bot parses and saves those schedule preferences before ranking courses or building plans.

Selectable-text PDFs are parsed directly. Screenshot/image-style PDFs are handled with a local OCR fallback, which is slower and may be less exact.

## Live-source notes

### VT timetable

The bot queries the official VT timetable endpoint directly. If that endpoint is unavailable, the bot can still fall back to the sample catalog.

### RateMyProfessors

The bot attempts to resolve the school ID automatically using the configured school name. If that does not work, set `RMP_SCHOOL_ID` manually in `.env`.

### Seat alerts

Seat alerts use the live timetable provider when available. If the live provider cannot determine availability, the bot falls back to the mock catalog.

## Useful commands

- `/profile`
- `/myprofile`
- `/uploaddars`
- `/planschedule`
- `/prefs`
- `/switchschedule`
- `/addclass`
- `/removeclass`
- `/clearschedule`
- `/schedule`
- `/viewschedule`
- `/privacy`
- `/addfriend`
- `/removefriend`
- `/free`
- `/recommend`
- `/professor`
- `/coursegrades`
- `/watchclass`
- `/unwatchclass`