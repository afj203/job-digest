#!/usr/bin/env python3
"""
Daily Job Digest
----------------
Searches ATS platforms via Google Custom Search API,
detects salary disclosure, and outputs an HTML digest.

QUICK START:
  1. pip install requests
  2. Fill in CONFIG below
  3. python job_digest.py --preview   <- see output with fake data (no API needed)
  4. python job_digest.py             <- real run
"""

import json
import os
import re
import datetime
import argparse
import requests

# ─────────────────────────────────────────────────────────────
#  CONFIG — fill these in
# ─────────────────────────────────────────────────────────────

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "YOUR_API_KEY_HERE")
GOOGLE_CX      = os.environ.get("GOOGLE_CX", "YOUR_SEARCH_ENGINE_ID_HERE")

SEEN_JOBS_FILE   = "seen_jobs.json"
DIGEST_HTML_FILE = "digest.html"

# ─────────────────────────────────────────────────────────────
#  SEARCH QUERIES — customize these
# ─────────────────────────────────────────────────────────────

ATS_SITES = (
    "site:boards.greenhouse.io OR "
    "site:jobs.lever.co OR "
    "site:jobs.ashbyhq.com OR "
    "site:myworkdayjobs.com OR "
    "site:jobs.smartrecruiters.com"
)

SEARCHES = [
    {
        "label": "Strategy & Insights — Remote",
        "query": (
            f"({ATS_SITES}) "
            "(insights OR competitive OR \"market research\" OR narrative OR \"competitive intelligence\") "
            "intitle:(analyst OR strategist OR strategy OR manager OR director) "
            "remote -intern -contract -hourly -warehouse"
        ),
    },
    # Add more searches here:
    # {
    #     "label": "Product Marketing — Remote",
    #     "query": (
    #         f"({ATS_SITES}) "
    #         "\"product marketing\" "
    #         "intitle:(manager OR director OR lead) "
    #         "remote -intern -contract"
    #     ),
    # },
]

MAX_RESULTS = 10


# ─────────────────────────────────────────────────────────────
#  SALARY DETECTION
# ─────────────────────────────────────────────────────────────

SALARY_PATTERNS = [
    r"\$[\d,]+[kK]?\s*[-\u2013\u2014to]+\s*\$?[\d,]+[kK]?",
    r"\$[\d,]+[kK]?\s*(per year|annually|\/yr|\/year)",
    r"[\d,]+\s*[-\u2013\u2014]\s*[\d,]+\s*(USD|usd)",
    r"salary.{0,30}\$[\d,]+",
    r"pay.{0,20}\$[\d,]+",
    r"compensation.{0,30}\$[\d,]+",
]

def detect_salary(text):
    for pattern in SALARY_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            start = max(0, match.start() - 10)
            end   = min(len(text), match.end() + 30)
            return text[start:end].strip()
    return None


# ─────────────────────────────────────────────────────────────
#  GOOGLE SEARCH
# ─────────────────────────────────────────────────────────────

def google_search(query):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key":          GOOGLE_API_KEY,
        "cx":           GOOGLE_CX,
        "q":            query,
        "num":          MAX_RESULTS,
        "dateRestrict": "d1",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("items", [])
    except requests.RequestException as e:
        print(f"  [ERROR] {e}")
        return []


# ─────────────────────────────────────────────────────────────
#  SEEN JOBS PERSISTENCE
# ─────────────────────────────────────────────────────────────

def load_seen():
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(list(seen), f, indent=2)


# ─────────────────────────────────────────────────────────────
#  SEARCH RUNNER
# ─────────────────────────────────────────────────────────────

ATS_DOMAINS = {
    "Greenhouse":      "greenhouse.io",
    "Lever":           "lever.co",
    "Ashby":           "ashbyhq.com",
    "Workday":         "myworkdayjobs.com",
    "SmartRecruiters": "smartrecruiters.com",
}

def guess_ats(url):
    for name, domain in ATS_DOMAINS.items():
        if domain in url:
            return name
    return "Unknown"

def run_searches(seen, dry_run=False):
    if dry_run:
        return _fake_results(), set()

    new_jobs = []
    new_urls = set()

    for search in SEARCHES:
        print(f"\n Searching: {search['label']}")
        results = google_search(search["query"])

        for item in results:
            url     = item.get("link", "")
            title   = item.get("title", "").strip()
            snippet = item.get("snippet", "").replace("\n", " ").strip()

            if url in seen or url in new_urls:
                continue

            new_jobs.append({
                "search_label": search["label"],
                "ats":          guess_ats(url),
                "title":        title,
                "url":          url,
                "snippet":      snippet,
                "salary":       detect_salary(snippet),
            })
            new_urls.add(url)

    return new_jobs, new_urls


def _fake_results():
    """Sample data for --preview mode. Edit to test formatting."""
    return [
        {
            "search_label": "Strategy & Insights — Remote",
            "ats":   "Greenhouse",
            "title": "Senior Competitive Intelligence Analyst",
            "url":   "https://boards.greenhouse.io/example/jobs/1234",
            "snippet": "Help shape our go-to-market strategy through deep competitive research. $95,000 - $120,000 per year. Remote.",
            "salary": "$95,000 - $120,000 per year",
        },
        {
            "search_label": "Strategy & Insights — Remote",
            "ats":   "Lever",
            "title": "Market Research Manager",
            "url":   "https://jobs.lever.co/example/abc-123",
            "snippet": "Lead qualitative and quantitative research programs to generate consumer insights. Fully remote role.",
            "salary": None,
        },
        {
            "search_label": "Strategy & Insights — Remote",
            "ats":   "Ashby",
            "title": "Director of Strategy",
            "url":   "https://jobs.ashbyhq.com/example/xyz-789",
            "snippet": "Own narrative strategy across product and marketing. Compensation: $140,000-$180,000 USD + equity.",
            "salary": "$140,000-$180,000 USD",
        },
        {
            "search_label": "Strategy & Insights — Remote",
            "ats":   "Workday",
            "title": "Business Insights Analyst",
            "url":   "https://company.myworkdayjobs.com/careers/job/Remote/Business-Insights-Analyst",
            "snippet": "Analyze market trends to drive strategic decisions across the business. 3+ years experience required.",
            "salary": None,
        },
    ]


# ─────────────────────────────────────────────────────────────
#  HTML OUTPUT — tweak CSS/layout here freely
# ─────────────────────────────────────────────────────────────

ATS_COLORS = {
    "Greenhouse":      "#24a148",
    "Lever":           "#4361ee",
    "Ashby":           "#7c3aed",
    "Workday":         "#e07b00",
    "SmartRecruiters": "#e63946",
    "Unknown":         "#888",
}

def ats_badge(ats):
    color = ATS_COLORS.get(ats, "#888")
    return f'<span class="badge" style="background:{color}">{ats}</span>'

def salary_tag(salary):
    if salary:
        return f'<span class="salary-tag">&#128176; {salary}</span>'
    return '<span class="no-salary">Salary not disclosed</span>'

def build_html(jobs, dry_run=False):
    now         = datetime.datetime.now().strftime("%A, %B %d %Y &ndash; %I:%M %p")
    total       = len(jobs)
    with_salary = sum(1 for j in jobs if j["salary"])

    by_label = {}
    for job in jobs:
        by_label.setdefault(job["search_label"], []).append(job)

    sections_html = ""
    for label, label_jobs in by_label.items():
        cards = ""
        for job in label_jobs:
            snippet_text = job["snippet"][:220] + ("..." if len(job["snippet"]) > 220 else "")
            cards += f"""
            <div class="card">
              <div class="card-meta">
                {ats_badge(job["ats"])}
                {salary_tag(job["salary"])}
              </div>
              <a class="job-title" href="{job["url"]}" target="_blank">{job["title"]}</a>
              <p class="snippet">{snippet_text}</p>
              <a class="apply-link" href="{job["url"]}" target="_blank">View posting &rarr;</a>
            </div>"""
        sections_html += f"""
        <section>
          <h2>{label} <span class="count">{len(label_jobs)}</span></h2>
          {cards}
        </section>"""

    preview_banner = """
        <div class="preview-banner">
          Preview mode &mdash; using sample data. Run without --preview for live results.
        </div>""" if dry_run else ""

    no_jobs = '<p class="empty">No new postings found since last run.</p>' if not jobs else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Job Digest</title>
  <style>
    :root {{
      --bg:          #f8f9fa;
      --surface:     #ffffff;
      --border:      #e5e7eb;
      --text:        #111827;
      --muted:       #6b7280;
      --accent:      #2563eb;
      --salary-bg:   #ecfdf5;
      --salary-text: #065f46;
      --no-sal:      #9ca3af;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg); color: var(--text);
      padding: 2rem 1rem;
    }}
    .preview-banner {{
      max-width: 780px; margin: 0 auto 1.25rem;
      background: #fef9c3; border: 1px solid #fde047;
      border-radius: 8px; padding: .75rem 1rem; font-size: .875rem;
    }}
    header {{
      max-width: 780px; margin: 0 auto 2rem;
      padding-bottom: 1.25rem; border-bottom: 2px solid var(--border);
    }}
    header h1 {{ font-size: 1.5rem; font-weight: 700; }}
    .meta {{
      margin-top: .4rem; font-size: .85rem; color: var(--muted);
      display: flex; gap: 1.5rem; flex-wrap: wrap;
    }}
    .meta strong {{ color: var(--text); }}
    section {{ max-width: 780px; margin: 0 auto 2.5rem; }}
    section h2 {{
      font-size: .8rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: .06em; color: var(--muted); margin-bottom: 1rem;
      display: flex; align-items: center; gap: .5rem;
    }}
    .count {{
      background: var(--border); color: var(--text);
      font-size: .72rem; padding: .1rem .45rem; border-radius: 99px;
    }}
    .card {{
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 10px; padding: 1.1rem 1.25rem; margin-bottom: .85rem;
      transition: box-shadow .15s;
    }}
    .card:hover {{ box-shadow: 0 4px 14px rgba(0,0,0,.07); }}
    .card-meta {{ display: flex; align-items: center; gap: .6rem; margin-bottom: .55rem; flex-wrap: wrap; }}
    .badge {{
      color: #fff; font-size: .68rem; font-weight: 700;
      padding: .18rem .5rem; border-radius: 99px; letter-spacing: .03em;
    }}
    .salary-tag {{
      font-size: .78rem; font-weight: 600;
      background: var(--salary-bg); color: var(--salary-text);
      padding: .18rem .55rem; border-radius: 99px;
    }}
    .no-salary {{ font-size: .78rem; color: var(--no-sal); }}
    .job-title {{
      display: block; font-size: 1rem; font-weight: 600;
      color: var(--accent); text-decoration: none; margin-bottom: .4rem; line-height: 1.4;
    }}
    .job-title:hover {{ text-decoration: underline; }}
    .snippet {{ font-size: .875rem; color: var(--muted); line-height: 1.55; margin-bottom: .65rem; }}
    .apply-link {{ font-size: .8rem; font-weight: 500; color: var(--accent); text-decoration: none; }}
    .apply-link:hover {{ text-decoration: underline; }}
    .empty {{ color: var(--muted); font-style: italic; max-width: 780px; margin: 0 auto; }}
  </style>
</head>
<body>
  {preview_banner}
  <header>
    <h1>Job Digest</h1>
    <div class="meta">
      <span>Generated <strong>{now}</strong></span>
      <span><strong>{total}</strong> new posting{"" if total == 1 else "s"}</span>
      <span><strong>{with_salary}</strong> with salary disclosed</span>
    </div>
  </header>
  {sections_html}
  {no_jobs}
</body>
</html>"""


# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true",
                        help="Use fake data to preview output without calling the API")
    args = parser.parse_args()

    seen = load_seen()
    print(f"Seen jobs loaded: {len(seen)}")

    new_jobs, new_urls = run_searches(seen, dry_run=args.preview)
    print(f"New jobs found: {len(new_jobs)}")

    html = build_html(new_jobs, dry_run=args.preview)
    with open(DIGEST_HTML_FILE, "w") as f:
        f.write(html)
    print(f"Digest written -> {DIGEST_HTML_FILE}")

    if not args.preview:
        seen.update(new_urls)
        save_seen(seen)
        print(f"Seen jobs updated: {len(seen)} total")

if __name__ == "__main__":
    main()
