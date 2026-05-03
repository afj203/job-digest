#!/usr/bin/env python3
"""
Daily Job Digest
----------------
Runs one query per ATS platform, detects salary and location,
outputs a digest of new jobs and a persistent all-time jobs table.

PREVIEW (no API key needed):
  python job_digest.py --preview

LIVE RUN:
  python job_digest.py
"""

import json
import os
import re
import datetime
import argparse
import requests

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────

GOOGLE_API_KEY   = os.environ.get("GOOGLE_API_KEY", "YOUR_API_KEY_HERE")
GOOGLE_CX        = os.environ.get("GOOGLE_CX", "YOUR_SEARCH_ENGINE_ID_HERE")

SEEN_JOBS_FILE   = "seen_jobs.json"
ALL_JOBS_FILE    = "all_jobs.json"
DIGEST_HTML_FILE = "digest.html"
ALL_JOBS_HTML    = "all_jobs.html"

MAX_RESULTS      = 10  # Google CSE max per request

# ─────────────────────────────────────────────────────────────
#  ATS PLATFORMS — one query will run per entry
# ─────────────────────────────────────────────────────────────

ATS_PLATFORMS = [
    {"name": "Greenhouse",      "site": "site:boards.greenhouse.io"},
    {"name": "Lever",           "site": "site:jobs.lever.co"},
    {"name": "Ashby",           "site": "site:jobs.ashbyhq.com"},
    {"name": "Workday",         "site": "site:myworkdayjobs.com"},
    {"name": "SmartRecruiters", "site": "site:jobs.smartrecruiters.com"},
    {"name": "iCIMS",           "site": "site:icims.com"},
    {"name": "BambooHR",        "site": "site:bamboohr.com"},
    {"name": "Workable",        "site": "site:apply.workable.com"},
    {"name": "Taleo",           "site": "site:taleo.net"},
    {"name": "Jobvite",         "site": "site:jobs.jobvite.com"},
]

# ─────────────────────────────────────────────────────────────
#  SEARCH KEYWORDS — applied to every ATS above
# ─────────────────────────────────────────────────────────────

KEYWORDS = (
    '(insights OR competitive OR "market research" OR narrative OR "competitive intelligence") '
    'intitle:(analyst OR strategist OR strategy OR manager OR director) '
    'remote -intern -contract -hourly -warehouse'
)

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
#  LOCATION DETECTION
# ─────────────────────────────────────────────────────────────

REMOTE_PATTERNS  = [r"\bremote\b", r"work from home", r"\bwfh\b", r"fully remote", r"100%\s*remote", r"distributed team"]
HYBRID_PATTERNS  = [r"\bhybrid\b", r"\d\s*days?.{0,10}office", r"flexible.{0,20}schedule", r"in-office some"]
ONSITE_PATTERNS  = [r"\bon.?site\b", r"\bin.office\b", r"in our .{0,30} office", r"\bheadquarters\b", r"\bonsite\b"]

def detect_location(text):
    t = text.lower()
    for p in HYBRID_PATTERNS:
        if re.search(p, t): return "Hybrid", "loc-hybrid"
    for p in ONSITE_PATTERNS:
        if re.search(p, t): return "On-site", "loc-onsite"
    for p in REMOTE_PATTERNS:
        if re.search(p, t): return "Remote", "loc-remote"
    return "Unknown", "loc-unknown"

# ─────────────────────────────────────────────────────────────
#  DOMAIN LABEL
# ─────────────────────────────────────────────────────────────

def domain_label(url):
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        if "myworkdayjobs.com" in host:
            return host
        first = [p for p in u.path.split("/") if p]
        return f"{host}/{first[0]}" if first else host
    except:
        return url

# ─────────────────────────────────────────────────────────────
#  GOOGLE SEARCH
# ─────────────────────────────────────────────────────────────

def google_search(query):
    params = {
        "key":          GOOGLE_API_KEY,
        "cx":           GOOGLE_CX,
        "q":            query,
        "num":          MAX_RESULTS,
        "dateRestrict": "d1",
    }
    try:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params, timeout=15
        )
        resp.raise_for_status()
        return resp.json().get("items", [])
    except requests.RequestException as e:
        print(f"  [ERROR] {e}")
        return []

# ─────────────────────────────────────────────────────────────
#  PERSISTENCE
# ─────────────────────────────────────────────────────────────

def load_seen():
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(list(seen), f, indent=2)

def load_all_jobs():
    if os.path.exists(ALL_JOBS_FILE):
        with open(ALL_JOBS_FILE) as f:
            return json.load(f)
    return []

def save_all_jobs(jobs):
    with open(ALL_JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)

# ─────────────────────────────────────────────────────────────
#  SEARCH RUNNER
# ─────────────────────────────────────────────────────────────

def run_searches(seen, dry_run=False):
    if dry_run:
        return _fake_results(), set()

    new_jobs = []
    new_urls = set()
    now      = datetime.datetime.now().isoformat()

    for ats in ATS_PLATFORMS:
        query = f"{ats['site']} {KEYWORDS}"
        print(f"  Searching {ats['name']}...")
        results = google_search(query)

        for item in results:
            url     = item.get("link", "")
            title   = item.get("title", "").strip()
            snippet = item.get("snippet", "").replace("\n", " ").strip()

            if url in seen or url in new_urls:
                continue

            loc, loc_cls = detect_location(snippet + " " + title)

            new_jobs.append({
                "found_at":    now,
                "ats":         ats["name"],
                "domain":      domain_label(url),
                "title":       title,
                "url":         url,
                "snippet":     snippet,
                "salary":      detect_salary(snippet),
                "loc":         loc,
                "loc_cls":     loc_cls,
            })
            new_urls.add(url)

        print(f"    {sum(1 for j in new_jobs if j['ats'] == ats['name'])} new")

    return new_jobs, new_urls


def _fake_results():
    now = datetime.datetime.now().isoformat()
    return [
        {"found_at": now, "ats": "Greenhouse", "domain": "boards.greenhouse.io/luminary",
         "title": "Senior Competitive Intelligence Analyst", "url": "https://boards.greenhouse.io/luminary/jobs/5923401",
         "snippet": "Shape go-to-market strategy through deep competitive research. $95,000 - $120,000 per year. Fully remote.",
         "salary": "$95,000 - $120,000 / yr", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "Lever", "domain": "jobs.lever.co/meridian-health",
         "title": "Market Research Manager", "url": "https://jobs.lever.co/meridian-health/a3f2c891",
         "snippet": "Lead qual and quant research programs. Hybrid schedule, 2 days/week in San Francisco office.",
         "salary": None, "loc": "Hybrid", "loc_cls": "loc-hybrid"},
        {"found_at": now, "ats": "Ashby", "domain": "jobs.ashbyhq.com/stackwise",
         "title": "Director of Strategy", "url": "https://jobs.ashbyhq.com/stackwise/b7d4e120",
         "snippet": "Own narrative strategy across product and marketing. Fully remote. Compensation: $140,000-$180,000 + equity.",
         "salary": "$140,000 - $180,000", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "Workday", "domain": "forgeanalytics.myworkdayjobs.com",
         "title": "Business Insights Analyst", "url": "https://forgeanalytics.myworkdayjobs.com/careers/job/JR-00291",
         "snippet": "Analyze market trends to drive strategic decisions. On-site role based at our Austin, TX headquarters.",
         "salary": None, "loc": "On-site", "loc_cls": "loc-onsite"},
        {"found_at": now, "ats": "SmartRecruiters", "domain": "jobs.smartrecruiters.com/novabridge",
         "title": "Competitive Strategy Manager", "url": "https://jobs.smartrecruiters.com/novabridge/743999001234567",
         "snippet": "Build competitive battle cards and win/loss analysis. Remote-first. Pay range: $110,000 - $135,000 annually.",
         "salary": "$110,000 - $135,000", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "iCIMS", "domain": "careers-crestline.icims.com/jobs",
         "title": "Narrative Strategist, Brand", "url": "https://careers-crestline.icims.com/jobs/1042",
         "snippet": "Develop and steward the brand voice and messaging architecture. Flexible hybrid model.",
         "salary": None, "loc": "Hybrid", "loc_cls": "loc-hybrid"},
        {"found_at": now, "ats": "BambooHR", "domain": "openloop.bamboohr.com/careers",
         "title": "Consumer Insights Analyst", "url": "https://openloop.bamboohr.com/careers/88",
         "snippet": "Surface the why behind consumer behavior. 100% remote. $75,000 - $90,000 + bonus.",
         "salary": "$75,000 - $90,000", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "Workable", "domain": "apply.workable.com/pinnacleops",
         "title": "GTM Strategy & Operations Lead", "url": "https://apply.workable.com/pinnacleops/j/C2E9A774/",
         "snippet": "Drive go-to-market planning and cross-functional alignment across sales, marketing, and product.",
         "salary": None, "loc": "Unknown", "loc_cls": "loc-unknown"},
    ]

# ─────────────────────────────────────────────────────────────
#  HTML: DIGEST (new jobs this run)
# ─────────────────────────────────────────────────────────────

def loc_tag(loc, cls):
    return f'<span class="badge {cls}">{loc}</span>'

def sal_tag(s):
    return (f'<span class="badge sal-yes">&#10003; {s}</span>'
            if s else '<span class="badge sal-no">No salary listed</span>')

SHARED_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font-sans,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif);color:var(--color-text-primary,#111);background:var(--color-background-tertiary,#f5f5f5);padding:1.5rem 1rem}
.wrap{max-width:820px;margin:0 auto}
.hdr{padding-bottom:1rem;border-bottom:1px solid var(--color-border-tertiary,#e5e5e5);margin-bottom:1.25rem}
.hdr h1{font-size:18px;font-weight:500;margin-bottom:.35rem}
.stats{display:flex;gap:1.25rem;flex-wrap:wrap;font-size:13px;color:var(--color-text-secondary,#666)}
.stats strong{color:var(--color-text-primary,#111);font-weight:500}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:1.25rem;align-items:center}
.filter-label{font-size:12px;color:var(--color-text-secondary,#666);margin-right:2px}
.filter-btn{font-size:12px;padding:4px 12px;border-radius:99px;border:1px solid #ccc;background:#fff;color:#555;cursor:pointer}
.filter-btn.active{background:#f0f0f0;color:#111;border-color:#999}
.section-lbl{font-size:11px;font-weight:500;letter-spacing:.07em;text-transform:uppercase;color:var(--color-text-secondary,#888);margin-bottom:.75rem;display:flex;align-items:center;gap:8px}
.pill-count{background:#e5e5e5;color:#555;font-size:11px;padding:2px 8px;border-radius:99px}
.card{background:#fff;border:1px solid #e5e5e5;border-radius:12px;padding:1rem 1.125rem;margin-bottom:.75rem}
.card:hover{border-color:#bbb}
.meta{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:.5rem}
.badge{font-size:11px;font-weight:500;padding:3px 9px;border-radius:99px}
.domain-badge{font-size:11px;padding:3px 9px;border-radius:99px;background:#f0f0f0;color:#555;font-family:monospace;border:1px solid #e0e0e0}
.loc-remote{background:#d1fae5;color:#065f46}
.loc-hybrid{background:#fef3c7;color:#78350f}
.loc-onsite{background:#fee2e2;color:#7f1d1d}
.loc-unknown{background:#f0f0f0;color:#888}
.sal-yes{background:#d1fae5;color:#065f46}
.sal-no{background:#f0f0f0;color:#aaa}
.job-title{font-size:15px;font-weight:500;color:#1a56db;text-decoration:none;display:block;margin-bottom:.35rem;line-height:1.4}
.job-title:hover{text-decoration:underline}
.snippet{font-size:13px;color:#666;line-height:1.6;margin-bottom:.6rem}
.view-link{font-size:12px;color:#1a56db;text-decoration:none}
.view-link:hover{text-decoration:underline}
.section{margin-bottom:2rem}
.empty{font-size:14px;color:#888;font-style:italic;padding:1rem 0}
.search-box{width:100%;padding:8px 12px;border:1px solid #ddd;border-radius:8px;font-size:14px;margin-bottom:1rem}
"""

def build_digest_html(new_jobs, dry_run=False):
    now         = datetime.datetime.now().strftime("%A, %B %d %Y &ndash; %I:%M %p")
    total       = len(new_jobs)
    with_salary = sum(1 for j in new_jobs if j["salary"])

    preview_banner = '<div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;padding:.75rem 1rem;font-size:.875rem;margin-bottom:1rem">Preview mode &mdash; using sample data.</div>' if dry_run else ""

    by_ats = {}
    for j in new_jobs:
        by_ats.setdefault(j["ats"], []).append(j)

    sections = ""
    for ats_name, jobs in by_ats.items():
        cards = "".join(f"""
        <div class="card" data-loc="{j['loc']}" data-sal="{'yes' if j['salary'] else 'no'}">
          <div class="meta">
            <span class="domain-badge">{j['domain']}</span>
            {loc_tag(j['loc'], j['loc_cls'])}
            {sal_tag(j['salary'])}
          </div>
          <a class="job-title" href="{j['url']}" target="_blank">{j['title']}</a>
          <p class="snippet">{j['snippet'][:240]}{'...' if len(j['snippet'])>240 else ''}</p>
          <a class="view-link" href="{j['url']}" target="_blank">View posting &rarr;</a>
        </div>""" for j in jobs)
        sections += f"""
        <div class="section" data-section>
          <div class="section-lbl">{ats_name}<span class="pill-count">{len(jobs)}</span></div>
          {cards}
        </div>"""

    no_jobs = '<p class="empty">No new postings found since last run.</p>' if not new_jobs else ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Digest</title><style>{SHARED_CSS}</style></head>
<body><div class="wrap">
{preview_banner}
<div class="hdr">
  <h1>Job digest &mdash; new this run</h1>
  <div class="stats">
    <span>Generated <strong>{now}</strong></span>
    <span id="ts"><strong>{total}</strong> new posting{"" if total==1 else "s"}</span>
    <span><strong>{with_salary}</strong> with salary disclosed</span>
    <span><a href="all_jobs.html" style="color:#1a56db">View all-time table &rarr;</a></span>
  </div>
</div>
<div class="filters">
  <span class="filter-label">Filter:</span>
  <button class="filter-btn active" onclick="filt('all',this)">All</button>
  <button class="filter-btn" onclick="filt('remote',this)">Remote only</button>
  <button class="filter-btn" onclick="filt('hybrid',this)">Hybrid</button>
  <button class="filter-btn" onclick="filt('salary',this)">Salary disclosed</button>
</div>
{sections}{no_jobs}
</div>
<script>
function filt(type,btn){{
  document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.card').forEach(c=>{{
    const show = type==='all'
      || (type==='remote' && c.dataset.loc==='Remote')
      || (type==='hybrid' && c.dataset.loc==='Hybrid')
      || (type==='salary' && c.dataset.sal==='yes');
    c.style.display = show ? '' : 'none';
  }});
  document.querySelectorAll('[data-section]').forEach(s=>{{
    const any = [...s.querySelectorAll('.card')].some(c=>c.style.display!=='none');
    s.style.display = any ? '' : 'none';
  }});
}}
</script>
</body></html>"""

# ─────────────────────────────────────────────────────────────
#  HTML: ALL-TIME TABLE
# ─────────────────────────────────────────────────────────────

def build_all_jobs_html(all_jobs):
    rows = ""
    for j in reversed(all_jobs):
        dt = j.get("found_at","")[:16].replace("T"," ")
        sal_cell = f'<span class="badge sal-yes">{j["salary"]}</span>' if j["salary"] else '<span style="color:#aaa;font-size:12px">—</span>'
        rows += f"""<tr
          data-loc="{j['loc']}"
          data-sal="{'yes' if j['salary'] else 'no'}"
          data-text="{(j['title']+' '+j['domain']+' '+j['ats']).lower()}">
          <td style="white-space:nowrap;color:#888;font-size:12px">{dt}</td>
          <td><span class="domain-badge">{j['domain']}</span></td>
          <td><a class="job-title" href="{j['url']}" target="_blank">{j['title']}</a></td>
          <td>{loc_tag(j['loc'], j['loc_cls'])}</td>
          <td>{sal_cell}</td>
        </tr>"""

    total = len(all_jobs)
    sal_count = sum(1 for j in all_jobs if j["salary"])

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>All Jobs</title>
<style>
{SHARED_CSS}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;font-size:11px;font-weight:500;letter-spacing:.05em;text-transform:uppercase;color:#888;padding:6px 10px;border-bottom:1px solid #e5e5e5;white-space:nowrap;cursor:pointer}}
th:hover{{color:#333}}
td{{padding:8px 10px;border-bottom:1px solid #f0f0f0;vertical-align:middle}}
tr:hover td{{background:#fafafa}}
.table-wrap{{background:#fff;border:1px solid #e5e5e5;border-radius:12px;overflow:hidden}}
</style></head>
<body><div class="wrap">
<div class="hdr">
  <h1>All jobs found</h1>
  <div class="stats">
    <span><strong id="showing">{total}</strong> of <strong>{total}</strong> total</span>
    <span><strong>{sal_count}</strong> with salary disclosed</span>
    <span><a href="digest.html" style="color:#1a56db">&larr; Latest digest</a></span>
  </div>
</div>

<input class="search-box" type="text" placeholder="Search by title, company, or ATS..." oninput="applyFilters()">

<div class="filters">
  <span class="filter-label">Location:</span>
  <button class="filter-btn active" onclick="setLoc('all',this)">All</button>
  <button class="filter-btn" onclick="setLoc('Remote',this)">Remote</button>
  <button class="filter-btn" onclick="setLoc('Hybrid',this)">Hybrid</button>
  <button class="filter-btn" onclick="setLoc('On-site',this)">On-site</button>
  &nbsp;
  <span class="filter-label">Salary:</span>
  <button class="filter-btn active" onclick="setSal('all',this)">All</button>
  <button class="filter-btn" onclick="setSal('yes',this)">Disclosed only</button>
</div>

<div class="table-wrap">
<table>
  <thead><tr>
    <th onclick="sortTable(0)">Found &#8597;</th>
    <th onclick="sortTable(1)">Company &#8597;</th>
    <th onclick="sortTable(2)">Title &#8597;</th>
    <th>Location</th>
    <th>Salary</th>
  </tr></thead>
  <tbody id="tbody">{rows}</tbody>
</table>
</div>
</div>
<script>
let locFilter='all', salFilter='all', sortCol=-1, sortAsc=true;

function setLoc(v,btn){{
  locFilter=v;
  document.querySelectorAll('.filters .filter-btn').forEach(b=>{{ if(b.onclick.toString().includes('setLoc')) b.classList.remove('active'); }});
  btn.classList.add('active');
  applyFilters();
}}
function setSal(v,btn){{
  salFilter=v;
  document.querySelectorAll('.filters .filter-btn').forEach(b=>{{ if(b.onclick.toString().includes('setSal')) b.classList.remove('active'); }});
  btn.classList.add('active');
  applyFilters();
}}
function applyFilters(){{
  const q = document.querySelector('.search-box').value.toLowerCase();
  let visible=0;
  document.querySelectorAll('#tbody tr').forEach(r=>{{
    const locOk = locFilter==='all' || r.dataset.loc===locFilter;
    const salOk = salFilter==='all' || r.dataset.sal===salFilter;
    const txtOk = !q || r.dataset.text.includes(q);
    const show  = locOk && salOk && txtOk;
    r.style.display = show ? '' : 'none';
    if(show) visible++;
  }});
  document.getElementById('showing').textContent = visible;
}}
function sortTable(col){{
  const tbody = document.getElementById('tbody');
  const rows  = [...tbody.querySelectorAll('tr')];
  if(sortCol===col) sortAsc=!sortAsc; else {{ sortCol=col; sortAsc=true; }}
  rows.sort((a,b)=>{{
    const at=a.cells[col].innerText.trim().toLowerCase();
    const bt=b.cells[col].innerText.trim().toLowerCase();
    return sortAsc ? at.localeCompare(bt) : bt.localeCompare(at);
  }});
  rows.forEach(r=>tbody.appendChild(r));
}}
</script>
</body></html>"""

# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true",
                        help="Use fake data — no API key needed")
    args = parser.parse_args()

    seen     = load_seen()
    all_jobs = load_all_jobs()
    print(f"Seen jobs loaded: {len(seen)} | All-time records: {len(all_jobs)}")

    new_jobs, new_urls = run_searches(seen, dry_run=args.preview)
    print(f"New jobs this run: {len(new_jobs)}")

    # Build and save digest
    with open(DIGEST_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(build_digest_html(new_jobs, dry_run=args.preview))
    print(f"Digest written -> {DIGEST_HTML_FILE}")

    # Append to all-time list and rebuild table
    if not args.preview:
        all_jobs.extend(new_jobs)
        save_all_jobs(all_jobs)
        seen.update(new_urls)
        save_seen(seen)

    with open(ALL_JOBS_HTML, "w", encoding="utf-8") as f:
        f.write(build_all_jobs_html(all_jobs if not args.preview else new_jobs))
    print(f"All-jobs table written -> {ALL_JOBS_HTML}")

    print("Done.")

if __name__ == "__main__":
    main()
