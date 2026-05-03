#!/usr/bin/env python3
"""
Daily Job Digest
----------------
- Runs one query per ATS platform via Brave Search API (10 queries per run)
- Detects salary and work location from snippets
- Emails an HTML digest via Gmail
- Saves a persistent all-time jobs table to all_jobs.html

PREVIEW MODE (no API key needed):
  python job_digest.py --preview

LIVE RUN:
  python job_digest.py
"""

import json
import os
import re
import datetime
import argparse
import smtplib
import requests
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────

BRAVE_API_KEY      = os.environ.get("BRAVE_API_KEY", "")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

SEEN_JOBS_FILE   = "seen_jobs.json"
ALL_JOBS_FILE    = "all_jobs.json"
DIGEST_HTML_FILE = "digest.html"
ALL_JOBS_HTML    = "all_jobs.html"
MAX_RESULTS      = 20   # Brave allows up to 20 per request

# ─────────────────────────────────────────────────────────────
#  ATS PLATFORMS — one search query runs per entry
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
#  SEARCH KEYWORDS
# ─────────────────────────────────────────────────────────────

KEYWORDS = (
    '(insights OR competitive OR "market research" OR narrative OR "competitive intelligence") '
    '(analyst OR strategist OR strategy OR manager OR director) '
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

HYBRID_PATTERNS = [r"\bhybrid\b", r"\d\s*days?.{0,10}(office|week)", r"flexible.{0,20}schedule"]
ONSITE_PATTERNS = [r"\bon.?site\b", r"\bin.office\b", r"in our .{0,30} office", r"\bheadquarters\b", r"\bonsite\b"]
REMOTE_PATTERNS = [r"\bremote\b", r"work from home", r"\bwfh\b", r"fully remote", r"100%\s*remote", r"distributed team"]

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
        u    = urlparse(url)
        host = u.hostname or ""
        if "myworkdayjobs.com" in host:
            return host
        parts = [p for p in u.path.split("/") if p]
        return f"{host}/{parts[0]}" if parts else host
    except:
        return url

# ─────────────────────────────────────────────────────────────
#  BRAVE SEARCH
# ─────────────────────────────────────────────────────────────

def brave_search(query):
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept":               "application/json",
                "Accept-Encoding":      "gzip",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
            params={
                "q":        query,
                "count":    MAX_RESULTS,
                "freshness": "pd",   # pd = past day
            },
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [
            {
                "link":    r.get("url", ""),
                "title":   r.get("title", ""),
                "snippet": r.get("description", ""),
            }
            for r in results
        ]
    except requests.RequestException as e:
   results = resp.json().get("web", {}).get("results", [])
        print(f"    Raw results from Brave: {len(results)}")
        print(f"    Full response keys: {list(resp.json().keys())}")
        return [
            {
                "link":    r.get("url", ""),
                "title":   r.get("title", ""),
                "snippet": r.get("description", ""),
            }
            for r in results
        ]
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
        print(f"  Querying {ats['name']}...")
        results = brave_search(query)
        found   = 0

        for item in results:
            url     = item.get("link", "")
            title   = item.get("title", "").strip()
            snippet = item.get("snippet", "").replace("\n", " ").strip()

            if url in seen or url in new_urls:
                continue

            loc, loc_cls = detect_location(snippet + " " + title)
            new_jobs.append({
                "found_at": now,
                "ats":      ats["name"],
                "domain":   domain_label(url),
                "title":    title,
                "url":      url,
                "snippet":  snippet,
                "salary":   detect_salary(snippet),
                "loc":      loc,
                "loc_cls":  loc_cls,
            })
            new_urls.add(url)
            found += 1

        print(f"    {found} new")

    return new_jobs, new_urls


def _fake_results():
    now = datetime.datetime.now().isoformat()
    return [
        {"found_at": now, "ats": "Greenhouse", "domain": "boards.greenhouse.io/luminary",
         "title": "Senior Competitive Intelligence Analyst",
         "url": "https://boards.greenhouse.io/luminary/jobs/5923401",
         "snippet": "Shape go-to-market strategy through deep competitive research. $95,000 - $120,000 per year. Fully remote.",
         "salary": "$95,000 - $120,000", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "Lever", "domain": "jobs.lever.co/meridian-health",
         "title": "Market Research Manager",
         "url": "https://jobs.lever.co/meridian-health/a3f2c891",
         "snippet": "Lead qual and quant research programs. Hybrid schedule, 2 days/week in San Francisco office.",
         "salary": None, "loc": "Hybrid", "loc_cls": "loc-hybrid"},
        {"found_at": now, "ats": "Ashby", "domain": "jobs.ashbyhq.com/stackwise",
         "title": "Director of Strategy",
         "url": "https://jobs.ashbyhq.com/stackwise/b7d4e120",
         "snippet": "Own narrative strategy. Fully remote. Compensation: $140,000-$180,000 + equity.",
         "salary": "$140,000 - $180,000", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "Workday", "domain": "forgeanalytics.myworkdayjobs.com",
         "title": "Business Insights Analyst",
         "url": "https://forgeanalytics.myworkdayjobs.com/careers/job/JR-00291",
         "snippet": "Analyze market trends. On-site role based at our Austin, TX headquarters.",
         "salary": None, "loc": "On-site", "loc_cls": "loc-onsite"},
        {"found_at": now, "ats": "SmartRecruiters", "domain": "jobs.smartrecruiters.com/novabridge",
         "title": "Competitive Strategy Manager",
         "url": "https://jobs.smartrecruiters.com/novabridge/743999001234567",
         "snippet": "Build competitive battle cards and win/loss analysis. Remote-first. Pay range: $110,000 - $135,000 annually.",
         "salary": "$110,000 - $135,000", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "iCIMS", "domain": "careers-crestline.icims.com/jobs",
         "title": "Narrative Strategist, Brand",
         "url": "https://careers-crestline.icims.com/jobs/1042",
         "snippet": "Develop brand voice and messaging architecture. Flexible hybrid model.",
         "salary": None, "loc": "Hybrid", "loc_cls": "loc-hybrid"},
        {"found_at": now, "ats": "BambooHR", "domain": "openloop.bamboohr.com/careers",
         "title": "Consumer Insights Analyst",
         "url": "https://openloop.bamboohr.com/careers/88",
         "snippet": "Surface the why behind consumer behavior. 100% remote. $75,000 - $90,000 + bonus.",
         "salary": "$75,000 - $90,000", "loc": "Remote", "loc_cls": "loc-remote"},
        {"found_at": now, "ats": "Workable", "domain": "apply.workable.com/pinnacleops",
         "title": "GTM Strategy & Operations Lead",
         "url": "https://apply.workable.com/pinnacleops/j/C2E9A774/",
         "snippet": "Drive go-to-market planning and cross-functional alignment. Location not specified.",
         "salary": None, "loc": "Unknown", "loc_cls": "loc-unknown"},
    ]

# ─────────────────────────────────────────────────────────────
#  SHARED STYLES
# ─────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #f5f5f5; color: #111; padding: 1.5rem 1rem; }
.wrap { max-width: 820px; margin: 0 auto; }
.hdr { padding-bottom: 1rem; border-bottom: 1px solid #e0e0e0; margin-bottom: 1.25rem; }
.hdr h1 { font-size: 18px; font-weight: 500; margin-bottom: .35rem; }
.stats { display: flex; gap: 1.25rem; flex-wrap: wrap; font-size: 13px; color: #666; }
.stats strong { color: #111; font-weight: 500; }
.stats a { color: #1a56db; text-decoration: none; }
.stats a:hover { text-decoration: underline; }
input[type=text] { width: 100%; padding: 8px 12px; border: 1px solid #ddd;
                   border-radius: 8px; font-size: 14px; margin-bottom: 1rem; background: #fff; color: #111; }
.filters { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 1.25rem; align-items: center; }
.flabel { font-size: 12px; color: #666; margin-right: 2px; }
.fbtn { font-size: 12px; padding: 4px 12px; border-radius: 99px;
        border: 1px solid #ccc; background: #fff; color: #555; cursor: pointer; }
.fbtn.on { background: #ebebeb; color: #111; border-color: #999; }
.section { margin-bottom: 2rem; }
.slbl { font-size: 11px; font-weight: 500; letter-spacing: .07em; text-transform: uppercase;
        color: #888; margin-bottom: .75rem; display: flex; align-items: center; gap: 8px; }
.scount { background: #e0e0e0; color: #555; font-size: 11px; padding: 2px 8px; border-radius: 99px; }
.card { background: #fff; border: 1px solid #e0e0e0; border-radius: 12px;
        padding: 1rem 1.125rem; margin-bottom: .75rem; }
.card:hover { border-color: #bbb; }
.meta { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-bottom: .5rem; }
.badge { font-size: 11px; font-weight: 500; padding: 3px 9px; border-radius: 99px; }
.dbadge { font-size: 11px; padding: 3px 9px; border-radius: 99px;
          background: #f0f0f0; color: #555; font-family: monospace; border: 1px solid #e0e0e0; }
.loc-remote  { background: #d1fae5; color: #065f46; }
.loc-hybrid  { background: #fef3c7; color: #78350f; }
.loc-onsite  { background: #fee2e2; color: #7f1d1d; }
.loc-unknown { background: #f0f0f0; color: #888; }
.sal-yes { background: #d1fae5; color: #065f46; }
.sal-no  { background: #f0f0f0; color: #aaa; }
.jtitle { font-size: 15px; font-weight: 500; color: #1a56db; text-decoration: none;
          display: block; margin-bottom: .35rem; line-height: 1.4; }
.jtitle:hover { text-decoration: underline; }
.snippet { font-size: 13px; color: #666; line-height: 1.6; margin-bottom: .6rem; }
.vlink { font-size: 12px; color: #1a56db; text-decoration: none; }
.vlink:hover { text-decoration: underline; }
.empty { font-size: 14px; color: #888; font-style: italic; padding: 1rem 0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-size: 11px; font-weight: 500; letter-spacing: .05em;
     text-transform: uppercase; color: #888; padding: 8px 10px;
     border-bottom: 1px solid #e0e0e0; white-space: nowrap; cursor: pointer; }
th:hover { color: #333; }
td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }
tr:hover td { background: #fafafa; }
.twrap { background: #fff; border: 1px solid #e0e0e0; border-radius: 12px; overflow: hidden; }
"""

# ─────────────────────────────────────────────────────────────
#  HTML HELPERS
# ─────────────────────────────────────────────────────────────

def loc_tag(loc, cls):
    return f'<span class="badge {cls}">{loc}</span>'

def sal_tag(s):
    return (f'<span class="badge sal-yes">&#10003; {s}</span>'
            if s else '<span class="badge sal-no">No salary listed</span>')

# ─────────────────────────────────────────────────────────────
#  BUILD DIGEST HTML
# ─────────────────────────────────────────────────────────────

def build_digest(new_jobs, dry_run=False):
    now         = datetime.datetime.now().strftime("%A, %B %d %Y &ndash; %I:%M %p ET")
    total       = len(new_jobs)
    with_salary = sum(1 for j in new_jobs if j["salary"])

    preview = ('<div style="background:#fef9c3;border:1px solid #fde047;border-radius:8px;'
               'padding:.75rem 1rem;font-size:.875rem;margin-bottom:1rem">'
               'Preview mode &mdash; using sample data.</div>') if dry_run else ""

    by_ats = {}
    for j in new_jobs:
        by_ats.setdefault(j["ats"], []).append(j)

    sections = ""
    for ats_name, jobs in by_ats.items():
        cards = "".join(f"""
        <div class="card" data-loc="{j['loc']}" data-sal="{'yes' if j['salary'] else 'no'}">
          <div class="meta">
            <span class="dbadge">{j['domain']}</span>
            {loc_tag(j['loc'], j['loc_cls'])}
            {sal_tag(j['salary'])}
          </div>
          <a class="jtitle" href="{j['url']}" target="_blank">{j['title']}</a>
          <p class="snippet">{j['snippet'][:240]}{'...' if len(j['snippet']) > 240 else ''}</p>
          <a class="vlink" href="{j['url']}" target="_blank">View posting &rarr;</a>
        </div>""" for j in jobs)

        sections += f"""
        <div class="section" data-sec>
          <div class="slbl">{ats_name}<span class="scount">{len(jobs)}</span></div>
          {cards}
        </div>"""

    no_jobs = '<p class="empty">No new postings found since last run.</p>' if not new_jobs else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Job Digest</title>
  <style>{CSS}</style>
</head>
<body>
<div class="wrap">
  {preview}
  <div class="hdr">
    <h1>Job digest &mdash; new this run</h1>
    <div class="stats">
      <span>Generated <strong>{now}</strong></span>
      <span><strong>{total}</strong> new posting{"" if total == 1 else "s"}</span>
      <span><strong>{with_salary}</strong> with salary disclosed</span>
    </div>
  </div>
  <div class="filters">
    <span class="flabel">Filter:</span>
    <button class="fbtn on" onclick="filt('all',this)">All</button>
    <button class="fbtn" onclick="filt('remote',this)">Remote only</button>
    <button class="fbtn" onclick="filt('hybrid',this)">Hybrid</button>
    <button class="fbtn" onclick="filt('salary',this)">Salary disclosed</button>
  </div>
  {sections}
  {no_jobs}
</div>
<script>
function filt(type, btn) {{
  document.querySelectorAll('.fbtn').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  document.querySelectorAll('.card').forEach(c => {{
    const show = type === 'all'
      || (type === 'remote' && c.dataset.loc === 'Remote')
      || (type === 'hybrid' && c.dataset.loc === 'Hybrid')
      || (type === 'salary' && c.dataset.sal === 'yes');
    c.style.display = show ? '' : 'none';
  }});
  document.querySelectorAll('[data-sec]').forEach(s => {{
    const any = [...s.querySelectorAll('.card')].some(c => c.style.display !== 'none');
    s.style.display = any ? '' : 'none';
  }});
}}
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────
#  BUILD ALL-JOBS HTML
# ─────────────────────────────────────────────────────────────

def build_all_jobs(all_jobs):
    total       = len(all_jobs)
    with_salary = sum(1 for j in all_jobs if j["salary"])

    rows = ""
    for j in reversed(all_jobs):
        dt  = j.get("found_at", "")[:16].replace("T", " ")
        sal = (f'<span class="badge sal-yes">{j["salary"]}</span>'
               if j["salary"] else '<span style="color:#aaa;font-size:12px">—</span>')
        txt = (j["title"] + " " + j["domain"] + " " + j["ats"]).lower().replace('"', '')
        rows += f"""<tr data-loc="{j['loc']}" data-sal="{'yes' if j['salary'] else 'no'}" data-txt="{txt}">
          <td style="white-space:nowrap;color:#888;font-size:12px">{dt}</td>
          <td><span class="dbadge">{j['domain']}</span></td>
          <td><a class="jtitle" href="{j['url']}" target="_blank">{j['title']}</a></td>
          <td>{loc_tag(j['loc'], j['loc_cls'])}</td>
          <td>{sal}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>All Jobs</title>
  <style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>All jobs found</h1>
    <div class="stats">
      <span>Showing <strong id="showing">{total}</strong> of <strong>{total}</strong> total</span>
      <span><strong>{with_salary}</strong> with salary disclosed</span>
    </div>
  </div>
  <input type="text" placeholder="Search by title, company..." oninput="apply()">
  <div class="filters">
    <span class="flabel">Location:</span>
    <button class="fbtn on" onclick="setL('all',this)">All</button>
    <button class="fbtn" onclick="setL('Remote',this)">Remote</button>
    <button class="fbtn" onclick="setL('Hybrid',this)">Hybrid</button>
    <button class="fbtn" onclick="setL('On-site',this)">On-site</button>
    &nbsp;
    <span class="flabel">Salary:</span>
    <button class="fbtn on" onclick="setS('all',this)">All</button>
    <button class="fbtn" onclick="setS('yes',this)">Disclosed only</button>
  </div>
  <div class="twrap">
    <table>
      <thead><tr>
        <th onclick="srt(0)">Found &#8597;</th>
        <th onclick="srt(1)">Company &#8597;</th>
        <th onclick="srt(2)">Title &#8597;</th>
        <th>Location</th>
        <th>Salary</th>
      </tr></thead>
      <tbody id="tbody">{rows}</tbody>
    </table>
  </div>
</div>
<script>
let lf='all', sf='all', sc=-1, sa=true;
function setL(v,btn){{lf=v;document.querySelectorAll('.fbtn').forEach(b=>{{if(b.onclick.toString().includes('setL'))b.classList.remove('on')}});btn.classList.add('on');apply();}}
function setS(v,btn){{sf=v;document.querySelectorAll('.fbtn').forEach(b=>{{if(b.onclick.toString().includes('setS'))b.classList.remove('on')}});btn.classList.add('on');apply();}}
function apply(){{
  const q=document.querySelector('input').value.toLowerCase();
  let n=0;
  document.querySelectorAll('#tbody tr').forEach(r=>{{
    const ok=(lf==='all'||r.dataset.loc===lf)&&(sf==='all'||r.dataset.sal===sf)&&(!q||r.dataset.txt.includes(q));
    r.style.display=ok?'':'none';
    if(ok)n++;
  }});
  document.getElementById('showing').textContent=n;
}}
function srt(col){{
  const tb=document.getElementById('tbody');
  const rows=[...tb.querySelectorAll('tr')];
  if(sc===col)sa=!sa;else{{sc=col;sa=true;}}
  rows.sort((a,b)=>{{
    const at=a.cells[col].innerText.trim().toLowerCase();
    const bt=b.cells[col].innerText.trim().toLowerCase();
    return sa?at.localeCompare(bt):bt.localeCompare(at);
  }});
  rows.forEach(r=>tb.appendChild(r));
}}
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────
#  EMAIL
# ─────────────────────────────────────────────────────────────

def send_email(digest_html, num_jobs):
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("Email skipped — credentials not configured.")
        return
    now     = datetime.datetime.now().strftime("%b %d %Y, %I:%M %p ET")
    subject = f"Job Digest — {num_jobs} new posting{'s' if num_jobs != 1 else ''} — {now}"
    msg     = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = GMAIL_ADDRESS
    msg.attach(MIMEText(digest_html, "html"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
        print("Email sent.")
    except Exception as e:
        print(f"Email failed: {e}")

# ─────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true",
                        help="Use sample data — no API key needed")
    args = parser.parse_args()

    seen     = load_seen()
    all_jobs = load_all_jobs()
    print(f"Seen: {len(seen)} | All-time: {len(all_jobs)}")

    new_jobs, new_urls = run_searches(seen, dry_run=args.preview)
    print(f"New jobs this run: {len(new_jobs)}")

    digest_html = build_digest(new_jobs, dry_run=args.preview)
    with open(DIGEST_HTML_FILE, "w", encoding="utf-8") as f:
        f.write(digest_html)
    print(f"Digest written -> {DIGEST_HTML_FILE}")

    if not args.preview:
        all_jobs.extend(new_jobs)
        seen.update(new_urls)
        save_all_jobs(all_jobs)
        save_seen(seen)

    display_jobs = all_jobs if not args.preview else new_jobs
    with open(ALL_JOBS_HTML, "w", encoding="utf-8") as f:
        f.write(build_all_jobs(display_jobs))
    print(f"All-jobs table written -> {ALL_JOBS_HTML}")

    send_email(digest_html, len(new_jobs))
    print("Done.")

if __name__ == "__main__":
    main()
