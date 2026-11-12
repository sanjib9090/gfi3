"""Flask resume-review prototype. JD definitions and fetched text live in JSON."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from io import BytesIO
from pathlib import Path
import base64
import hashlib
import json
import mimetypes
import os
import re
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from flask import Flask, jsonify, request, send_file, send_from_directory
from urllib.parse import urlsplit, quote
from pypdf import PdfReader
from striprtf.striprtf import rtf_to_text

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / 'data' / 'roles.json'
UPLOAD_LIMIT = int(3.9 * 1024 * 1024)
app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024
_CACHE = None

class StorageConfigurationError(Exception):
    pass

def storage_backend():
    """Use Supabase for all saved files, both locally and on Vercel."""
    supabase_settings()
    return 'supabase'

# Cache the bucket privacy check briefly, not the resumes or the secret itself.
_BUCKET_CHECK = {'fingerprint': None, 'checked_at': 0}

def supabase_settings():
    url = os.environ.get('SUPABASE_URL', 'https://uhixysyutemcszibpgyv.supabase.co').rstrip('/')
    key = os.environ.get('SUPABASE_SECRET_KEY', '') or os.environ.get('SUPABASE_KEY', '') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '')
    bucket = os.environ.get('SUPABASE_BUCKET', 'candidate-review')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise StorageConfigurationError('Set SUPABASE_URL to the HTTPS project URL without a path.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', bucket):
        raise StorageConfigurationError('Set SUPABASE_BUCKET to a valid private bucket name.')
    if not key or key.startswith(('sb_publishable_', 'anon')):
        raise StorageConfigurationError('Set SUPABASE_KEY or SUPABASE_SECRET_KEY to a server secret key (sb_secret_...), not a publishable key.')
    if not (key.startswith('sb_secret_') or key.count('.') == 2):
        raise StorageConfigurationError('Use a Supabase secret key or legacy service-role JWT, not a publishable key.')
    if not key.startswith('sb_secret_'):
        try:
            payload = key.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            if claims.get('role') != 'service_role':
                raise ValueError('Not a service-role key')
        except (ValueError, IndexError, TypeError, AttributeError):
            raise StorageConfigurationError('Use a Supabase secret key or legacy service-role JWT, not an anon key.') from None
    headers = {'apikey': key}
    # Modern secret keys are not JWTs and must not be sent as a Bearer token.
    if not key.startswith('sb_secret_'):
        headers['Authorization'] = 'Bearer ' + key
    return url, bucket, headers

def supabase_request(method, path, **kwargs):
    """Talk to the private storage API from Flask, never from the browser."""
    url, bucket, headers = supabase_settings()
    fingerprint = hashlib.sha256((url + bucket + headers['apikey']).encode()).hexdigest()
    now = time.monotonic()
    try:
        if _BUCKET_CHECK['fingerprint'] != fingerprint or now - _BUCKET_CHECK['checked_at'] > 30:
            info = requests.get(url + '/storage/v1/bucket/' + quote(bucket, safe=''), headers=headers, timeout=(5, 15))
            info.raise_for_status()
            bucket_info = info.json()
            if not isinstance(bucket_info, dict) or bucket_info.get('public') is not False:
                raise StorageConfigurationError('The Supabase bucket must be private. Change its access settings before continuing.')
            _BUCKET_CHECK.update(fingerprint=fingerprint, checked_at=now)
        supplied_headers = kwargs.pop('headers', {})
        response = requests.request(method, url + '/storage/v1/' + path, headers={**headers, **supplied_headers}, timeout=(5, 25), **kwargs)
        if response.status_code == 404 and method == 'GET' and path.startswith('object/authenticated/'):
            raise FileNotFoundError('Supabase object not found')
        response.raise_for_status()
        return response
    except FileNotFoundError:
        raise
    except StorageConfigurationError:
        raise
    except (requests.RequestException, ValueError, TypeError):
        raise StorageConfigurationError('Supabase storage is unavailable. Check the private bucket, project URL, server key, and network.') from None

def supabase_path(kind, key=''):
    _, bucket, _ = supabase_settings()
    return kind + '/' + quote(bucket, safe='') + ('/' + quote(key, safe='/') if key else '')

def valid_key(key):
    if not re.fullmatch(r'[a-z0-9_./-]+', key) or '..' in key or key.startswith('/'):
        raise ValueError('Invalid storage key.')
    return key

def storage_put(key, data, content_type='application/octet-stream'):
    key = valid_key(key)
    supabase_request('POST', supabase_path('object', key), data=data,
                     headers={'Content-Type': content_type, 'x-upsert': 'true'})

def storage_get(key):
    key = valid_key(key)
    return supabase_request('GET', supabase_path('object/authenticated', key)).content

def storage_list(prefix):
    prefix = valid_key(prefix)
    names, offset, limit = [], 0, 100
    while True:
        response = supabase_request('POST', supabase_path('object/list'), json={
            'prefix': prefix.rstrip('/'), 'limit': limit, 'offset': offset,
            'sortBy': {'column': 'name', 'order': 'asc'}
        })
        try:
            page = response.json()
            if not isinstance(page, list):
                raise ValueError('Invalid list response')
        except ValueError:
            raise StorageConfigurationError('Supabase returned an invalid file listing.') from None
        for item in page:
            name = item.get('name', '')
            if re.fullmatch(r'[a-z0-9_.-]+', name):
                names.append(valid_key(prefix.rstrip('/') + '/' + name))
        if len(page) < limit:
            return names
        offset += len(page)

def storage_delete(key):
    key = valid_key(key)
    supabase_request('DELETE', supabase_path('object'), json={'prefixes': [key]})

def save_json(key, value):
    storage_put(key, json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8'), 'application/json')

def read_json(key):
    return json.loads(storage_get(key).decode('utf-8'))

@app.before_request
def protect_app():
    """Block cross-origin changes. This prototype has no user login."""
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        origin = request.headers.get('Origin')
        if origin and urlsplit(origin).netloc.lower() != request.host.lower():
            return jsonify({'error': 'Cross-origin changes are not allowed.'}), 403
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify({'error': 'Cross-origin changes are not allowed.'}), 403

@app.errorhandler(StorageConfigurationError)
def storage_error(exc):
    return jsonify({'error': str(exc)}), 503

@app.errorhandler(413)
def too_large(_):
    return jsonify({'error': 'File exceeds the 3.9 MB upload limit.'}), 413

# These are generic page-parsing steps; URLs, criteria, weights, and JD text are JSON data.
def parse_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.h1.get_text(' ', strip=True) if soup.h1 else ''
    blocks = soup.select('.tally-text')
    if not title or not blocks:
        raise ValueError('The expected Tally job-description blocks were not found.')
    lines = []
    for block in blocks:
        for item in block.contents:
            if isinstance(item, NavigableString):
                text = str(item)
            elif isinstance(item, Tag):
                text = item.get_text(' ', strip=True)
            else:
                continue
            for part in re.split(r'\n+|(?=\s+\*\s)', text):
                line = re.sub(r'\s+', ' ', part).strip()
                if line and line not in lines:
                    lines.append(line)
    if sum(map(len, lines)) < 500:
        raise ValueError('The Tally page contains too little readable text.')
    return {'title': title, 'lines': lines}

def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))

def fetch_job(key, role):
    url = role['url']
    # Only the configured Tally URLs are accepted, not URLs supplied by a web request.
    if not re.fullmatch(r'https://tally\.so/r/[A-Za-z0-9]+', url):
        raise ValueError('Only configured https://tally.so/r/... job pages are supported.')
    # Read the previously fetched JD first. It persists across Vercel cold starts.
    # Never overwrite an existing saved JD merely because someone opened the app.
    try:
        saved = read_json(f'jds/{key}.json')
    except FileNotFoundError:
        saved = None
    except (ValueError, TypeError):
        raise StorageConfigurationError(f'Saved {key} job description is invalid. Check the JSON in the private bucket.') from None
    if saved is not None:
        if not isinstance(saved, dict) or not isinstance(saved.get('lines'), list) or not saved.get('title'):
            raise StorageConfigurationError(f'Saved {key} job description is invalid. Check the JSON in the private bucket.')
        saved.update(source='cached', url=url)
        return saved
    try:
        with requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; CandidateReview/1.0)'},
                          timeout=(4, 9), allow_redirects=False, stream=True) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Unexpected HTTP status.')
            chunks, size = [], 0
            for chunk in response.iter_content(32768):
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError('Tally page exceeds the response limit.')
                chunks.append(chunk)
        jd = parse_html(b''.join(chunks))
        supported = sum(any(re.search(rule['jd_pattern'], line, re.I) for line in jd['lines']) for rule in role['rules'])
        if supported < 3:
            raise ValueError('The live job description cannot be mapped reliably.')
    except (requests.RequestException, ValueError, TypeError):
        jd = json.loads((ROOT / 'data' / f'jd_{key}.json').read_text(encoding='utf-8'))
        jd.update(source='snapshot', http_status=None,
                  warning='Live Tally fetch failed; the bundled JSON may be out of date.')
    else:
        jd.update(source='cached', http_status=200, fetched_at=datetime.now(timezone.utc).isoformat(), url=url)
        # Save once and reuse this copy on subsequent page loads and cold starts.
        save_json(f'jds/{key}.json', jd)
    jd['url'] = url
    return jd

def job_context(force=False):
    global _CACHE
    if not force and _CACHE and time.time() - _CACHE[0] < 300:
        return _CACHE[1]
    config = load_config()
    with ThreadPoolExecutor(max_workers=2) as pool:
        fetched = dict(zip(config, pool.map(lambda key: fetch_job(key, config[key]), config)))
    output, material = {}, {}
    for key, role in config.items():
        jd = fetched[key]
        selected = []
        for rule in role['rules']:
            matches = [line for line in jd['lines'] if re.search(rule['jd_pattern'], line, re.I)]
            if matches:
                selected.append({**rule, 'jdEvidence': matches[:2]})
        if len(selected) < 3:
            # Do not silently score an incomplete description.
            raise ValueError(f'The extracted {key} job description cannot be mapped reliably; review roles.json and the Tally page.')
        requirements = [line for line in jd['lines'] if len(line) > 25 and re.search(role['requirement_pattern'], line, re.I)]
        output[key] = {**role, 'rules': selected, 'jd': {k: v for k, v in jd.items() if k != 'lines'}, 'requirements': requirements[:35]}
        material[key] = {'lines': jd['lines'], 'rules': selected}
    version = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    _CACHE = (time.time(), (output, version))
    return _CACHE[1]

# Section aliases handle common resume formats without assuming one template.
SECTION_ALIASES = {
    'experience': {'experience', 'work experience', 'professional experience', 'employment history', 'work history', 'career history', 'professional background', 'internship experience', 'industry experience', 'relevant experience', 'employment experience', 'experience and internships', 'work experience and internships', 'professional experience and internships'},
    'projects': {'projects', 'project', 'projects deliverables', 'projects and deliverables', 'project experience', 'project work', 'academic projects', 'personal projects', 'selected projects', 'key projects', 'relevant projects', 'portfolio projects', 'project highlights', 'academic and personal projects'},
    'education': {'education', 'educational background', 'academic background', 'academic qualifications', 'education qualifications', 'educational qualifications', 'qualifications', 'academic history', 'academic details', 'education and training'},
    'skills': {'skills', 'technical skills', 'skills tools', 'skills and tools', 'core skills', 'key skills', 'professional skills', 'technical expertise', 'technical proficiencies', 'competencies', 'core competencies', 'skills and competencies', 'tools technologies', 'tools and technologies', 'technologies', 'technical skills and tools', 'skill set', 'skills and technologies', 'tools and software', 'software and tools'},
    '_other': {'profile', 'profile summary', 'summary', 'professional summary', 'about me', 'objective', 'career objective', 'certifications', 'certifications licenses', 'certifications and licenses', 'certificates', 'awards', 'achievements', 'achievement', 'awards honors', 'awards and honors', 'languages', 'interests', 'hobbies', 'volunteering', 'references', 'publications'}
}
HEADING_MAP = {name: key for key, names in SECTION_ALIASES.items() for name in names}

def normalize_heading(value):
    value = value.strip().lower().replace('&', ' and ').replace('/', ' ')
    value = re.sub(r'[^\w\s]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()

def extract_sections(text):
    sections = {k: [] for k in ('experience', 'projects', 'education', 'skills')}
    found = set()
    current = None
    for raw in text.splitlines():
        line = re.sub(r'\s+', ' ', raw).strip()
        if not line:
            continue
        heading_part, separator, remainder = line.partition(':')
        # Only treat a colon as a heading if its left side is a known heading.
        key = HEADING_MAP.get(normalize_heading(heading_part if separator else line))
        if key:
            current = None if key == '_other' else key
            if current:
                found.add(current)
                if separator and remainder.strip():
                    sections[current].append(remainder.strip())
            continue
        if current:
            sections[current].append(re.sub(r'^[•●▪◦·‣⁃]\s*', '', line))
    # Conservative evidence-only fallback when headings are missing; never invent a role or credential.
    inferred = {
        'education': r'\b(?:b\.?tech|m\.?tech|b\.?sc|m\.?sc|mba|mca|bachelor(?:s|\'s)?|master(?:s|\'s)?|university degree|ph\.?d)\b',
        'skills': r'\b(?:figma|sketch|adobe xd|simapro|gabi|openlca|python|java|javascript|react|html|css|sql|wireframes?|prototyp\w*|usability testing|life cycle assessment)\b',
        'projects': r'\b(?:capstone|academic project|personal project|project portfolio|case study)\b',
        'experience': r'\b(?:intern|engineer|consultant|designer|developer|analyst|manager)\b.{0,80}\b(?:19|20)\d{2}\b|(?<!no )(?<!without )\b(?:experience|experienced)\s+(?:in|with)\s+[a-z]'
    }
    status = {}
    for key in sections:
        if key in found:
            status[key] = 'labeled'
        else:
            raw_lines = text.splitlines()
            matches = []
            for i, line in enumerate(raw_lines):
                if re.search(inferred[key], line, re.I):
                    snippet = line.strip()
                    if key == 'experience' and snippet.endswith(',') and i + 1 < len(raw_lines):
                        snippet += ' ' + raw_lines[i + 1].strip()
                    matches.append(snippet)
            sections[key] = matches[:12]
            status[key] = 'inferred' if matches else 'missing'
    return sections, status

# Dated work history is an estimate, not a verified claim of independent LCA work.
MONTHS = {name: i for i, names in enumerate((('jan','january'), ('feb','february'), ('mar','march'), ('apr','april'), ('may',), ('jun','june'), ('jul','july'), ('aug','august'), ('sep','sept','september'), ('oct','october'), ('nov','november'), ('dec','december')), 1) for name in names}
MONTH_PATTERN = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
def date_part(prefix):
    return rf'(?:(?:(?P<{prefix}_month>{MONTH_PATTERN})[.,]?\s+)|(?:(?P<{prefix}_number>0?[1-9]|1[0-2])[/.-]))?(?P<{prefix}_year>(?:19|20)\d{{2}}|[’\']?\d{{2}})'
DATE_RANGE = re.compile(rf'(?<![\d/]){date_part("start")}\s*(?:[-–—]|\bto\b)\s*(?:{date_part("end")}|(?P<present>present|current|now))(?!\w)', re.I)
SHARED_YEAR = re.compile(rf'(?P<start_month>{MONTH_PATTERN})\s*(?:[-–—]|\bto\b)\s*(?P<end_month>{MONTH_PATTERN})\s+(?P<end_year>(?:19|20)\d{{2}}|[’\']\d{{2}})\b', re.I)
DURATION = re.compile(r'\b(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*(years?|months?)\b', re.I)
TITLE_HINT = re.compile(r'\b(?:engineer|analyst|consultant|specialist|scientist|manager|developer|designer|director|trainee|intern|internship|researcher|associate|writer|entrepreneur|lead|officer|freelanc(?:e|er)|coordinator|architect|assistant|executive)\b', re.I)
NON_TITLE_PREFIX = re.compile(r'^(?:conducted|performed|managed|designed|developed|worked|working|built|led|provided|created|collaborated|contributed|responsible|increased|improved|supported|achieved|assisted|prepared|applied|maintained|utilized|performed)\b', re.I)

def _year(value):
    cleaned = value.lstrip("'’")
    if len(cleaned) == 2:
        return (2000 if int(cleaned) < 50 else 1900) + int(cleaned)
    return int(cleaned)

def _parse_range(line, today):
    m = DATE_RANGE.search(line)
    shared = False
    if not m:
        m = SHARED_YEAR.search(line)
        shared = bool(m)
    if not m:
        return None
    if shared:
        ey = sy = _year(m.group('end_year'))
        sm, em = MONTHS[m.group('start_month').lower()], MONTHS[m.group('end_month').lower()]
        if sm > em:  # year boundary is ambiguous, so do not invent the prior year
            return None
        start, end = date(sy, sm, 1), date(ey, em, monthrange(ey, em)[1]) + timedelta(days=1)
    else:
        if len(m.group('start_year').lstrip("'’")) == 2 and not (m.group('start_month') or m.group('start_number')):
            return None
        if not m.group('present') and len(m.group('end_year').lstrip("'’")) == 2 and not (m.group('end_month') or m.group('end_number')):
            return None
        sy = _year(m.group('start_year'))
        sm = int(m.group('start_number') or 0) or MONTHS.get((m.group('start_month') or '').lower(), 1)
        start = date(sy, sm, 1)
        if m.group('present'):
            end = today
        else:
            ey = _year(m.group('end_year'))
            em = int(m.group('end_number') or 0) or MONTHS.get((m.group('end_month') or '').lower(), 12)
            end = date(ey, em, monthrange(ey, em)[1]) + timedelta(days=1)
            if ey == today.year and not m.group('end_month') and not m.group('end_number'):
                end = min(end, today)
    if start >= end or start > today or end > today + timedelta(days=1) or sy < 1950:
        return None
    return {'start': start, 'end': end, 'match': m}

def find_jobs(lines, today=None):
    """Identify date-bearing work entries inside a labelled experience section."""
    today = today or datetime.now(timezone.utc).date()
    entries = []
    for i, line in enumerate(lines):
        if len(line) > 240:
            continue
        parsed = _parse_range(line, today)
        duration = DURATION.search(line) if not parsed else None
        if not parsed and not duration:
            continue
        match = parsed['match'] if parsed else duration
        before = line[:match.start()].strip(' :-–—|,')
        if NON_TITLE_PREFIX.search(before):
            continue
        # A date embedded in descriptive prose is not a job header.
        if before and (len(before.split()) < 2 or not (TITLE_HINT.search(before) or re.search(r'[—|]', before))):
            continue
        start_index = i
        title = line
        if not before:
            if not i or len(lines[i-1]) > 160 or NON_TITLE_PREFIX.search(lines[i-1]):
                continue
            start_index = i-1
            title = lines[i-1] + ' · ' + line
            if not TITLE_HINT.search(lines[i-1]) and i > 1 and TITLE_HINT.search(lines[i-2]) and not NON_TITLE_PREFIX.search(lines[i-2]):
                start_index = i-2
                title = lines[i-2] + ' · ' + title
        # A duration-only heading must have a clear work role.
        if duration and not TITLE_HINT.search(title):
            continue
        entries.append({'index': start_index, 'date_index': i, 'title': title, 'parsed': parsed, 'duration': duration})
    for i, entry in enumerate(entries):
        end = entries[i+1]['index'] if i+1 < len(entries) else len(lines)
        # An undated new title still ends the preceding job; its duties cannot
        # be credited to an earlier dated role.
        for j in range(entry['date_index']+1, end):
            line = lines[j]
            if len(line) <= 140 and TITLE_HINT.search(line) and not NON_TITLE_PREFIX.search(line) and (re.search(r'[—|]', line) or re.match(r'^(?:senior|junior|lead|principal|project|product|ui|ux|lca|graduate|research|freelance)\b', line, re.I)):
                end = j
                break
        entry['body'] = lines[entry['date_index']+1:end]
    return entries

def aggregate_jobs(entries, title_patterns=(), duty_patterns=(), today=None, all_roles=False):
    today = today or datetime.now(timezone.utc).date()
    intervals, evidence, durations = [], [], []
    for entry in entries:
        title = entry['title']
        matched_title = any(re.search(p, title, re.I) for p in title_patterns)
        duty = next((line for line in entry['body'] if any(re.search(p, line, re.I) for p in duty_patterns)), None)
        if not all_roles and not matched_title and not duty:
            continue
        if entry['parsed']:
            intervals.append((entry['parsed']['start'], entry['parsed']['end']))
            evidence.append(title)
            if duty and not matched_title:
                evidence.append(duty)
        elif entry['duration']:
            duration = entry['duration']
            val = float(duration.group(1)) / (12 if duration.group(2).lower().startswith('month') else 1)
            durations.append((val, title))
    if not intervals:
        if not durations:
            return None
        years, claim = max(durations, key=lambda x:x[0])
        return {'years': years, 'evidence': [claim], 'as_of': today.isoformat(), 'kind': 'claimed', 'duration_only': True}
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    months = sum((end.year-start.year)*12 + end.month-start.month + (end.day-start.day)/31 for start, end in merged)
    return {'years': max(0, months / 12), 'evidence': evidence[:8], 'as_of': today.isoformat(), 'kind': 'estimated', 'duration_only_omitted': bool(durations)}

def estimate_experience(section_lines, role_patterns, generic_role_pattern='', duty_patterns=(), today=None):
    return aggregate_jobs(find_jobs(section_lines, today), role_patterns, duty_patterns, today)

def explicit_total_claim(lines):
    # Use only a concrete duration attached to an experience claim. Do not
    # infer employment from projects, qualifications, or a vague summary.
    pattern = re.compile(r'\b(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*(years?|months?)\s+(?:of\s+)?(?:(?:professional|work|industry|hands[ -]on|relevant|design|engineering|research|R&D|LCA|UI|UX|UI/UX)\s+){0,3}experience\b', re.I)
    matches = []
    for line in lines:
        if match := pattern.search(line):
            years = float(match.group(1)) / (12 if match.group(2).lower().startswith('month') else 1)
            if 0 < years <= 60:
                matches.append((years, line))
    if not matches:
        return None
    years, evidence = max(matches, key=lambda x: x[0])
    return {'years': years, 'evidence': [evidence], 'as_of': None, 'kind': 'claimed'}

def evaluate(candidate, roles):
    text = candidate.get('text', '')
    lines = [re.sub(r'\s+', ' ', s).strip() for s in text.splitlines() if s.strip()]
    results = {}
    experience_info = None
    sections, status = extract_sections(text)
    jobs = find_jobs(sections['experience']) if status['experience'] == 'labeled' else []
    total_experience = aggregate_jobs(jobs, all_roles=True) or explicit_total_claim(lines)
    role_experience = {key: aggregate_jobs(jobs, role.get('experience_title_patterns', []), role.get('experience_duty_patterns', [])) for key, role in roles.items()}
    for key, role in roles.items():
        criteria = []
        for rule in role['rules']:
            evidence = [line for line in lines if any(re.search(p, line, re.I) for p in rule['patterns'])][:3]
            match_type = 'full' if evidence else 'none'
            if not evidence and rule.get('partial_patterns'):
                evidence = [line for line in lines if any(re.search(p, line, re.I) for p in rule['partial_patterns'])][:3]
                if evidence:
                    match_type = 'partial'
            points = rule['weight'] if match_type == 'full' else rule.get('partial_points', 0) if match_type == 'partial' else 0
            note = None
            if rule.get('years_points'):
                years = [float(m.group(1)) for line in evidence if (m := re.search(r'(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*years?', line, re.I))]
                claim = max(years, default=0)
                estimate = role_experience.get(key)
                if estimate and estimate['years'] > claim:
                    n = estimate['years']
                    evidence = estimate['evidence']
                    note = (f"{'Claimed' if estimate['kind']=='claimed' else 'Estimated'} {n:.1f} years from roles with LCA titles or duties" + (f" as of {estimate['as_of']}" if estimate['kind']=='estimated' else '') + "; overlapping dated roles are counted once. "
                            "Role tenure does not establish when LCA work began or its intensity. Verify dates, duties and independent project work.")
                    experience_info = {'kind': estimate['kind'], 'years': round(n, 1), 'asOf': estimate['as_of'] if estimate['kind']=='estimated' else None, 'evidence': evidence, 'note': note}
                else:
                    n = claim
                    if n:
                        note = f"Resume claims {n:g} years of LCA experience. Verify dates and independent project work."
                        experience_info = {'kind': 'claimed', 'years': n, 'asOf': None, 'evidence': evidence, 'note': note}
                points = next((bucket['points'] for bucket in rule['years_points'] if n >= bucket['min']), 0)
                match_type = ('full' if points == rule['weight'] else 'partial') if n else 'none'
            if match_type == 'none' and rule.get('review_patterns'):
                review_evidence = [line for line in lines if any(re.search(p, line, re.I) for p in rule['review_patterns'])][:3]
                if review_evidence:
                    evidence = review_evidence
                    match_type = 'review'
                    note = rule.get('review_note', 'Verify this evidence manually.')
            criteria.append({'label': rule['label'], 'weight': rule['weight'], 'matched': bool(evidence) and points > 0, 'matchType': match_type, 'points': points, 'evidence': evidence, 'note': note})
        maximum = sum(c['weight'] for c in criteria)
        results[key] = {'criteria': criteria, 'score': round(100 * sum(c['points'] for c in criteria) / maximum) if maximum else 0}
    a, b = results['lca']['score'], results['design']['score']
    if candidate.get('error'):
        classification = 'Uncertain'
    elif a < 30 and b < 30:
        classification = 'Neither / uncertain'
    elif a >= 30 and b >= 30 and abs(a - b) < 15:
        classification = 'Both'
    else:
        classification = 'LCA' if a > b else 'UI/UX'
    if experience_info:
        role_experience['lca'] = {**experience_info, 'as_of': experience_info.get('asOf')}
    return {**candidate, 'results': results, 'classification': classification, 'sections': sections, 'sectionStatus': status, 'lcaExperience': experience_info, 'experienceByRole': role_experience, 'totalExperience': total_experience}

def check_zip(data):
    with zipfile.ZipFile(BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 25 * 1024 * 1024:
            raise ValueError('Document expands beyond the 25 MB limit.')

def extract_docx(data):
    check_zip(data)
    doc = Document(BytesIO(data))
    lines = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn('w:p'):
            line = Paragraph(child, doc).text.strip()
            if line:
                lines.append(line)
        elif child.tag == qn('w:tbl'):
            for row in Table(child, doc).rows:
                cells = [cell.text.strip().replace('\n', ' | ') for cell in row.cells]
                if any(cells):
                    lines.append(' | '.join(cells))
    return '\n'.join(lines)

def decode_text(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', errors='replace')
    return data.decode('utf-8-sig', errors='replace')

def extract_file(filename, data):
    suffix = Path(filename).suffix.lower()
    if suffix in ('.txt', '.md'):
        return decode_text(data)
    if suffix in ('.html', '.htm'):
        soup = BeautifulSoup(data, 'html.parser')
        for node in soup(['script', 'style', 'noscript']):
            node.decompose()
        return soup.get_text('\n', strip=True)
    if suffix == '.rtf':
        return rtf_to_text(data.decode('utf-8', errors='replace'))
    if suffix == '.docx':
        return extract_docx(data)
    if suffix == '.odt':
        check_zip(data)
        with zipfile.ZipFile(BytesIO(data)) as archive:
            root = ET.fromstring(archive.read('content.xml'))
        namespace = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
        return '\n'.join(''.join(node.itertext()) for node in root.iter() if node.tag in (namespace + 'p', namespace + 'h'))
    if suffix == '.pdf':
        pdf = PdfReader(BytesIO(data))
        if pdf.is_encrypted:
            raise ValueError('Password-protected PDFs are not supported.')
        if len(pdf.pages) > 50:
            raise ValueError('PDF exceeds the 50-page limit.')
        return '\n'.join(page.extract_text() or '' for page in pdf.pages)
    raise ValueError('Unsupported type. Use PDF, DOCX, ODT, TXT, Markdown, RTF or HTML.')

def saved_records():
    records, warnings = [], []
    for key in storage_list('records/'):
        if not key.endswith('.json'):
            continue
        try:
            item = read_json(key)
            if not re.fullmatch(r'[0-9a-f]{32}', item['id']):
                raise ValueError('Invalid record ID')
            records.append(item)
        except (ValueError, KeyError, json.JSONDecodeError):
            warnings.append('A saved resume record could not be read; check the server storage.')
    return records, warnings

@app.get('/')
def index():
    return send_from_directory(ROOT / 'public', 'index.html')

@app.get('/gfi-logo.jpg')
def logo():
    return send_from_directory(ROOT / 'public', 'gfi-logo.jpg')

@app.get('/assets/<path:filename>')
def asset(filename):
    return send_from_directory(ROOT / 'public' / 'assets', filename)

@app.get('/api/candidates')
def candidates():
    try:
        storage_backend()
        roles, version = job_context()
        supplied = json.loads((ROOT / 'data' / 'resumes.json').read_text(encoding='utf-8'))
        # Match the exact original filename to the preview observed in WorkDrive.
        # Uploaded resumes continue to use their saved file, not this mapping.
        links_file = ROOT / 'data' / 'resume_links.json'
        source_links = json.loads(links_file.read_text(encoding='utf-8')) if links_file.exists() else {}
        for candidate in supplied:
            if candidate.get('origin') == 'supplied':
                candidate['source_url'] = source_links.get(candidate.get('filename'))
        uploaded, warnings = saved_records()
        public_roles = {k: {**v, 'rules': [{'label': r['label'], 'weight': r['weight'], 'jdEvidence': r['jdEvidence']} for r in v['rules']]} for k, v in roles.items()}
        response = jsonify({'candidates': [evaluate(c, roles) for c in supplied + uploaded], 'roles': public_roles, 'analysis_version': version, 'warnings': warnings, 'storage': storage_backend()})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except StorageConfigurationError:
        raise
    except Exception:
        app.logger.exception('Could not load job descriptions or saved resumes')
        return jsonify({'error': 'Could not load job descriptions or saved resumes. Check server logs and storage configuration.'}), 503

@app.post('/api/upload')
def upload():
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'Choose a supported resume file.'}), 400
    filename = Path(f.filename.replace('\\', '/')).name[:240]
    try:
        storage_backend()
        roles, version = job_context()
        if request.form.get('analysis_version') != version:
            return jsonify({'error': 'The job descriptions changed. Refresh the page before uploading.'}), 409
        data = f.read(UPLOAD_LIMIT + 1)
        if len(data) > UPLOAD_LIMIT:
            return jsonify({'error': 'File exceeds the 3.9 MB upload limit.'}), 413
        text = extract_file(filename, data)
        warning = None if len(text.strip()) >= 100 else 'Little or no text could be extracted. This may be a scan; inspect the source or upload a text-based file.'
        identifier = uuid.uuid4().hex
        suffix = Path(filename).suffix.lower()
        file_key = f'resumes/{identifier}{suffix}'
        item = {'id': identifier, 'filename': filename, 'origin': 'upload', 'text': text, 'error': warning,
                'file_key': file_key, 'stored_at': datetime.now(timezone.utc).isoformat()}
        storage_put(file_key, data, mimetypes.guess_type(filename)[0] or 'application/octet-stream')
        try:
            save_json(f'records/{identifier}.json', item)
        except Exception:
            storage_delete(file_key)
            raise
        response = jsonify(evaluate(item, roles))
        response.headers['Cache-Control'] = 'no-store'
        return response
    except StorageConfigurationError:
        raise
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Could not process or store uploaded resume')
        return jsonify({'error': 'Could not read or save this file. It may be damaged, encrypted, unsupported, or storage may be unavailable.'}), 400

def record_for_id(identifier):
    if not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise FileNotFoundError(identifier)
    return read_json(f'records/{identifier}.json')

@app.get('/api/resumes/<identifier>/file')
def view_resume(identifier):
    try:
        item = record_for_id(identifier)
        contents = storage_get(item['file_key'])
        suffix = Path(item['filename']).suffix.lower()
        mime = 'application/pdf' if suffix == '.pdf' else 'text/plain' if suffix in ('.txt', '.md') else 'application/octet-stream'
        response = send_file(BytesIO(contents), mimetype=mime, as_attachment=suffix not in ('.pdf', '.txt', '.md'), download_name=item['filename'])
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    except FileNotFoundError:
        return jsonify({'error': 'Resume not found.'}), 404

@app.delete('/api/resumes/<identifier>')
def delete_resume(identifier):
    try:
        item = record_for_id(identifier)
        storage_delete(item['file_key'])
        storage_delete(f'records/{identifier}.json')
        return jsonify({'ok': True})
    except FileNotFoundError:
        return jsonify({'error': 'Resume not found.'}), 404

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
