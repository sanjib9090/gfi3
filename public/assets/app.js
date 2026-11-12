/* Candidate Review frontend.
   This is plain JavaScript. Flask does the extraction and scoring. */

// The data currently shown on the page.
var candidates = [];
var roles = {};
var selectedRole = 'lca';
var selectedCandidateId = null;
var analysisVersion = '';

// Shortcuts for the HTML elements we use often.
var uploadButton = document.getElementById('upload-button');
var fileInput = document.getElementById('file-input');
var searchInput = document.getElementById('search');
var filterInput = document.getElementById('filter');
var sortInput = document.getElementById('sort');
var detail = document.getElementById('detail');

// Resume text comes from uploaded files, so escape it before inserting HTML.
function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function safeLink(url) {
  try {
    var parsed = new URL(url);
    if (parsed.protocol === 'https:') return escapeHtml(parsed.href);
  } catch (error) {
    // A bad link should not stop the rest of the page from working.
  }
  return '#';
}

function candidateName(candidate) {
  if (candidate.origin === 'supplied') {
    var found = candidate.filename.match(/^anonymized_(.+?)_\d+\.docx$/i);
    return found ? found[1] : 'Unnamed';
  }
  return candidate.filename.replace(/\.[^.]+$/, '');
}

function showNotice(message) {
  document.getElementById('notice-area').innerHTML =
    '<div class="notice" role="status"><span>' + escapeHtml(message) +
    '</span><button id="dismiss-notice" aria-label="Dismiss message">×</button></div>';
  document.getElementById('dismiss-notice').onclick = function () {
    document.getElementById('notice-area').innerHTML = '';
  };
}

function showError(error) {
  showNotice(error.message || 'Something went wrong. Please try again.');
}

// Read an API response and turn server errors into readable messages.
async function readResponse(response) {
  var data;
  try {
    data = await response.json();
  } catch (error) {
    throw new Error('The server returned an unexpected response.');
  }
  if (!response.ok) throw new Error(data.error || 'The server could not complete the request.');
  return data;
}

async function loadCandidates() {
  var loadingScreen = document.getElementById('initial-loading');
  var controller = new AbortController();
  // Avoid leaving the loading screen spinning indefinitely after a network failure.
  var timeout = setTimeout(function () { controller.abort(); }, 45000);
  var slowMessage = setTimeout(function () {
    document.getElementById('loading-help').textContent =
      'This is taking longer than expected. Please wait; if it fails, refresh the page.';
  }, 12000);
  try {
    var response = await fetch('/api/candidates', { signal: controller.signal });
    var data = await readResponse(response);
    candidates = data.candidates;
    roles = data.roles;
    analysisVersion = data.analysis_version;
    uploadButton.disabled = false;
    uploadButton.innerHTML = '<span>＋</span> Upload a resume';
    if (data.warnings && data.warnings.length) showNotice(data.warnings.join(' '));
    renderPage();
    loadingScreen.hidden = true;
  } catch (error) {
    uploadButton.innerHTML = '<span>＋</span> Upload unavailable';
    document.getElementById('loading-title').textContent = 'Unable to load candidates';
    document.getElementById('loading-message').textContent = error.name === 'AbortError'
      ? 'Loading took too long. Please refresh the page and try again.'
      : (error.message || 'The data could not be loaded').replace(/[.!?]+$/, '') + '. Please refresh the page and try again.';
    document.getElementById('loading-help').textContent =
      'If the problem continues, check the Supabase configuration and connection.';
    document.getElementById('loading-spinner').hidden = true;
    document.getElementById('loading-refresh').hidden = false;
    loadingScreen.setAttribute('role', 'alert');
  } finally {
    clearTimeout(timeout);
    clearTimeout(slowMessage);
  }
}

document.getElementById('loading-refresh').onclick = function () {
  window.location.reload();
};

// Saved data is shown as saved, not as a fresh request to Tally.
function jobSourceLabel(jd) {
  if (jd.source === 'cached') return 'Saved Tally data' + (jd.http_status === 200 ? ' · HTTP 200' : '');
  if (jd.source === 'live') return 'Tally fetched · HTTP 200';
  return 'Bundled JSON · live fetch unavailable';
}

function countCandidates(test) {
  return candidates.filter(test).length;
}

function renderSummary() {
  var supplied = countCandidates(function (c) { return c.origin === 'supplied'; });
  var uploaded = countCandidates(function (c) { return c.origin === 'upload'; });
  var lca = countCandidates(function (c) { return c.classification === 'LCA' || c.classification === 'Both'; });
  var design = countCandidates(function (c) { return c.classification === 'UI/UX' || c.classification === 'Both'; });
  var review = countCandidates(function (c) { return Boolean(c.error); });
  var description = supplied + ' supplied' + (uploaded ? ' + ' + uploaded + ' uploaded' : '');
  document.getElementById('summary').innerHTML =
    '<div class="summary-item"><span>RESUMES IN REVIEW</span><strong>' + candidates.length + '</strong><small>' + description + '</small></div>' +
    '<div class="summary-item"><span>LCA OR BOTH</span><strong>' + lca + '</strong><small>Evidence-based classification</small></div>' +
    '<div class="summary-item"><span>UI/UX OR BOTH</span><strong>' + design + '</strong><small>Evidence-based classification</small></div>' +
    '<div class="summary-item warning"><span>NEEDS EXTRACTION REVIEW</span><strong>' + String(review).padStart(2, '0') + '</strong><small>Check source documents</small></div>';
}

function renderRole() {
  var role = roles[selectedRole];
  if (!role) {
    document.getElementById('role-info').innerHTML = '';
    return;
  }
  var jd = role.jd || {};
  var source = jobSourceLabel(jd);
  var fetchedAt = jd.fetched_at ? ' ' + new Date(jd.fetched_at).toLocaleString() : '';
  var warning = jd.warning ? '<div class="inline-alert"><b>Source warning:</b> ' + escapeHtml(jd.warning) + '</div>' : '';
  var experience = '';
  if (selectedRole === 'lca') {
    var requirement = (role.requirements || []).find(function (line) { return /years of experience/i.test(line); });
    experience = '<div class="inline-alert"><b>Experience check:</b> ' + escapeHtml(role.context_note || '') + ' ' +
      escapeHtml(requirement || 'Check the current job description for its experience requirement.') +
      ' Verify manually; no automatic cutoff.</div>';
  }
  document.getElementById('role-info').innerHTML =
    '<div class="role-context"><div><b>' + escapeHtml(role.name) + '</b><span>' + escapeHtml(role.description) + '</span></div>' +
    '<a href="' + safeLink(role.url) + '" target="_blank" rel="noopener noreferrer">View job brief ↗</a></div>' +
    '<div class="jd-bar"><div><span class="jd-dot' + (jd.source === 'snapshot' ? ' stale' : '') + '"></span><b>' + escapeHtml(source) + '</b><small>' + escapeHtml(fetchedAt) + '</small></div>' +
    '<button id="compare-button">Compare job requirements ↗</button></div>' + warning + experience;
  document.getElementById('compare-button').onclick = showRequirements;
}

function setRole(role) {
  selectedRole = role;
  ['lca', 'design'].forEach(function (name) {
    var tab = document.getElementById(name + '-tab');
    tab.classList.toggle('active', name === role);
    tab.setAttribute('aria-selected', name === role ? 'true' : 'false');
  });
  renderRole();
  renderList();
  renderDetail();
}

// Ranking always uses score first. Filters do not change a candidate's rank.
function rankedCandidates() {
  var sorted = candidates.slice();
  sorted.sort(function (a, b) {
    return b.results[selectedRole].score - a.results[selectedRole].score || a.filename.localeCompare(b.filename);
  });
  return sorted.map(function (candidate, index) {
    return { candidate: candidate, rank: index + 1 };
  });
}

function renderList() {
  var search = searchInput.value.toLowerCase();
  var filter = filterInput.value;
  var list = rankedCandidates().filter(function (item) {
    var c = item.candidate;
    var matchesFilter = filter === 'all' || (filter === 'review' ? Boolean(c.error) : c.classification === filter);
    var matchesSearch = (c.filename + ' ' + (c.text || '')).toLowerCase().includes(search);
    return matchesFilter && matchesSearch;
  });
  if (sortInput.value === 'name') {
    list.sort(function (a, b) { return a.candidate.filename.localeCompare(b.candidate.filename); });
  }
  document.getElementById('result-count').textContent = list.length;
  var html = '';
  list.forEach(function (item) {
    var c = item.candidate;
    var name = candidateName(c);
    var caption = c.error ? 'Extraction needs review' : (c.origin === 'upload' ? 'New upload' : 'Supplied resume');
    html += '<button class="candidate' + (selectedCandidateId === c.id ? ' selected' : '') + '" data-candidate-id="' + escapeHtml(c.id) + '">' +
      '<span class="rank">' + String(item.rank).padStart(2, '0') + '</span>' +
      '<span class="avatar">' + escapeHtml(name.slice(0, 2).toUpperCase()) + '</span>' +
      '<span class="candidate-name"><b>' + escapeHtml(name) + '</b><small>' + escapeHtml(caption) + '</small></span>' +
      '<span class="tag' + (c.error ? ' tag-warn' : '') + '">' + escapeHtml(c.classification) + '</span>' +
      '<span class="score"><b>' + c.results[selectedRole].score + '</b><small>/ 100</small></span><span class="arrow">›</span></button>';
  });
  document.getElementById('candidate-list').innerHTML = html || '<div class="empty">No candidates match these filters.</div>';
}

function selectCandidate(id) {
  selectedCandidateId = id;
  renderList();
  renderDetail();
  if (window.matchMedia('(max-width: 1000px)').matches) detail.scrollIntoView({ block: 'start' });
}

// Highlight a date in experience evidence, without changing its meaning.
function highlightDates(text) {
  var escaped = escapeHtml(text);
  return escaped.replace(/\b(?:\d{1,2}[\/.\-])?(?:19|20)\d{2}(?:\s*(?:[-–—]|to)\s*(?:(?:\d{1,2}[\/.\-])?(?:19|20)\d{2}|present|current|now))?|\b\d+(?:\.\d+)?\s*(?:\+|plus)?\s*years?\b/gi, '<mark>$&</mark>');
}

function experienceStat(label, info) {
  var html = '<div class="experience-stat"><span>' + escapeHtml(label) + '</span>';
  if (!info) return html + '<p class="experience-missing">Not identified</p></div>';
  var date = info.as_of || info.asOf;
  var description = info.kind === 'claimed' ? 'Claimed duration' : 'Estimated from dates';
  if (date) description += ' · as of ' + date;
  return html + '<strong>' + Number(info.years).toFixed(1) + '<small> years</small></strong><p>' + escapeHtml(description) + '</p></div>';
}

function experienceEvidence(label, info) {
  if (!info || !info.evidence || !info.evidence.length) return '';
  var html = '<div><b>' + escapeHtml(label) + '</b><ul>';
  info.evidence.forEach(function (line) { html += '<li>' + highlightDates(line) + '</li>'; });
  html += '</ul>';
  if (info.duration_only_omitted) html += '<p>Undated duration claims are not added to dated roles because overlap is unknown.</p>';
  return html + '</div>';
}

function renderExperience(candidate, role) {
  var related = (candidate.experienceByRole || {})[selectedRole];
  var total = candidate.totalExperience;
  var label = 'Relevant to ' + role.short;
  var evidence = experienceEvidence(label, related) + experienceEvidence('Recorded work & training', total);
  return '<section class="experience-card" aria-label="Experience highlights"><div class="eyebrow">EXPERIENCE HIGHLIGHTS · VERIFY IN RESUME</div>' +
    '<div class="experience-grid">' + experienceStat(label, related) + experienceStat('Recorded work & training', total) + '</div>' +
    '<p class="experience-caution">Work, internships and training may be included. Role duration does not prove time spent on relevant duties; verify dates and context.</p>' +
    (evidence ? '<details class="experience-evidence"><summary>View experience evidence</summary>' + evidence + '</details>' : '') + '</section>';
}

function renderCriteria(candidate, role) {
  var html = '';
  candidate.results[selectedRole].criteria.forEach(function (criterion) {
    var icon = criterion.matched ? '✓' : '–';
    var iconClass = criterion.matched ? 'tick' : 'miss';
    html += '<div class="criterion"><div><span class="' + iconClass + '">' + icon + '</span><b>' +
      escapeHtml(criterion.label) + '</b><strong>' + criterion.points + ' / ' + criterion.weight + '</strong></div>';
    (criterion.evidence || []).forEach(function (line) {
      var label = criterion.matchType === 'review' ? 'Needs verification' :
        (criterion.matchType === 'partial' ? 'Partial evidence' : 'Resume');
      var text = criterion.label === 'LCA experience (claimed or estimated)' ? highlightDates(line) : escapeHtml(line);
      html += '<p><b>' + label + ':</b> ' + text + '</p>';
    });
    if (criterion.note) {
      var noteLabel = criterion.matchType === 'review' ? 'Review required' : 'Experience note';
      html += '<p class="hint"><b>' + noteLabel + ':</b> ' + escapeHtml(criterion.note) + '</p>';
    }
    var rule = (role.rules || []).find(function (item) { return item.label === criterion.label; });
    if (rule && rule.jdEvidence && rule.jdEvidence.length) {
      html += '<p class="jd-evidence"><b>JD:</b> ' + escapeHtml(rule.jdEvidence[0]) + '</p>';
    }
    html += '</div>';
  });
  return html;
}

function renderSections(candidate) {
  var sections = candidate.sections || {};
  var statuses = candidate.sectionStatus || {};
  var items = [['Experience', 'experience'], ['Projects', 'projects'], ['Education', 'education'], ['Skills & tools', 'skills']];
  var html = '';
  items.forEach(function (item) {
    var title = item[0];
    var key = item[1];
    var lines = sections[key] || [];
    var status = statuses[key];
    var count = lines.length ? lines.length + ' line' + (lines.length === 1 ? '' : 's') + (status === 'inferred' ? ' · inferred' : '') : 'Not identified';
    html += '<details><summary>' + title + ' <span>' + escapeHtml(count) + '</span></summary>';
    if (status === 'inferred') html += '<p class="hint">Potential matches inferred from text; verify them in the resume.</p>';
    if (lines.length) {
      html += '<ul>';
      lines.forEach(function (line) { html += '<li>' + (key === 'experience' ? highlightDates(line) : escapeHtml(line)) + '</li>'; });
      html += '</ul>';
    } else {
      html += '<p class="hint">No matching section found. This does not mean the information is absent; check the source.</p>';
    }
    html += '</details>';
  });
  return html + '<details><summary>Full extracted text <span>Review parsing</span></summary><pre class="resume-text">' +
    escapeHtml(candidate.text || 'No text extracted.') + '</pre></details>';
}

function renderDetail() {
  var candidate = candidates.find(function (item) { return item.id === selectedCandidateId; });
  var role = roles[selectedRole];
  if (!candidate || !role) {
    detail.innerHTML = '<div class="detail-empty"><div class="empty-icon">↗</div><h3>Look closer</h3>' +
      '<p>Select a candidate to see the evidence behind their score, extracted details and source document.</p><span>SELECT A CANDIDATE TO BEGIN</span></div>';
    return;
  }
  var rank = rankedCandidates().find(function (item) { return item.candidate.id === candidate.id; }).rank;
  var name = candidateName(candidate);
  // Supplied resumes use a verified, filename-specific WorkDrive link.
  // New uploads open their own saved files instead.
  var source;
  if (candidate.origin === 'supplied') {
    if (candidate.source_url && safeLink(candidate.source_url) !== '#') {
      source = '<a href="' + safeLink(candidate.source_url) + '" target="_blank" rel="noopener noreferrer">View resume ↗</a>';
    } else {
      source = '<span class="source-unavailable">Resume link not available</span>';
    }
  } else {
    source = '<a href="/api/resumes/' + encodeURIComponent(candidate.id) + '/file" target="_blank" rel="noopener noreferrer">View saved resume ↗</a>';
  }
  var remove = candidate.origin === 'upload' ? '<button id="remove-upload">Remove upload</button>' : '';
  var warning = candidate.error ? '<div class="inline-alert"><b>Extraction warning:</b> ' + escapeHtml(candidate.error) + '</div>' : '';
  var score = candidate.results[selectedRole].score;
  detail.innerHTML =
    '<div class="detail-top"><div class="eyebrow">CANDIDATE DETAIL <span> / </span> #' + String(rank).padStart(2, '0') + '</div>' +
    '<button class="close-detail" id="close-detail" aria-label="Close candidate details">×</button></div>' +
    '<div class="person"><div class="avatar large">' + escapeHtml(name.slice(0, 2).toUpperCase()) + '</div>' +
    '<div><h2>' + escapeHtml(name) + '</h2><p>' + escapeHtml(candidate.filename) + '</p></div></div>' +
    '<div class="detail-links">' + source + remove + '</div>' + warning +
    '<div class="scorecard"><div><div class="eyebrow">' + escapeHtml(role.short) + ' EVIDENCE SCORE</div>' +
    '<strong>' + score + '<small> / 100</small></strong><p>Classified as <b>' + escapeHtml(candidate.classification) + '</b></p></div>' +
    '<div class="score-circle">' + score + '<small>PTS</small></div></div>' +
    '<div class="compare-scores"><span>Role comparison</span><b>LCA ' + candidate.results.lca.score + ' / 100</b><b>UI/UX ' + candidate.results.design.score + ' / 100</b></div>' +
    renderExperience(candidate, role) +
    '<div class="detail-section"><h3>Score breakdown</h3><p class="hint">Points reflect text matches, not verified ability.</p>' + renderCriteria(candidate, role) + '</div>' +
    '<div class="detail-section"><h3>Extracted information</h3>' + renderSections(candidate) + '</div>' +
    '<p class="fineprint">Resume extraction and keyword matching can miss context. Confirm evidence and requirements in the original before making a decision.</p>';
  document.getElementById('close-detail').onclick = function () {
    selectedCandidateId = null;
    renderList();
    renderDetail();
  };
  if (candidate.origin === 'upload') document.getElementById('remove-upload').onclick = function () { removeUpload(candidate); };
}

function renderPage() {
  renderSummary();
  renderRole();
  renderList();
  renderDetail();
}

async function uploadFiles(files) {
  if (!files.length) return;
  uploadButton.disabled = true;
  uploadButton.innerHTML = '<span>＋</span> Reading resumes…';
  var successes = 0;
  var errors = [];
  for (var i = 0; i < files.length; i++) {
    var file = files[i];
    try {
      if (file.size > 3.9 * 1024 * 1024) throw new Error('File exceeds the 3.9 MB upload limit.');
      var form = new FormData();
      form.append('file', file);
      form.append('analysis_version', analysisVersion);
      var response = await fetch('/api/upload', { method: 'POST', body: form });
      var candidate = await readResponse(response);
      candidates.push(candidate);
      selectedCandidateId = candidate.id;
      successes++;
      if (candidate.error) errors.push(file.name + ': ' + candidate.error);
    } catch (error) {
      errors.push(file.name + ': ' + error.message);
    }
  }
  renderPage();
  var message = successes ? successes + ' file' + (successes === 1 ? '' : 's') + ' saved. ' : '';
  showNotice(message + errors.join(' '));
  uploadButton.disabled = false;
  uploadButton.innerHTML = '<span>＋</span> Upload a resume';
  fileInput.value = '';
  if (successes && window.matchMedia('(max-width: 1000px)').matches) detail.scrollIntoView({ block: 'start' });
}

async function removeUpload(candidate) {
  if (!window.confirm('Permanently remove this uploaded resume?')) return;
  try {
    var response = await fetch('/api/resumes/' + encodeURIComponent(candidate.id), { method: 'DELETE' });
    await readResponse(response);
    candidates = candidates.filter(function (item) { return item.id !== candidate.id; });
    selectedCandidateId = null;
    renderPage();
    showNotice('Uploaded resume removed.');
  } catch (error) {
    showError(error);
  }
}

// A shared dialog helper keeps both information windows simple.
function openModal(title, body, extraClass) {
  var area = document.getElementById('modal-area');
  area.innerHTML = '<div class="overlay" id="modal-overlay"><div class="modal ' + (extraClass || '') + '" role="dialog" aria-modal="true" aria-label="' + escapeHtml(title) + '">' +
    '<div class="detail-top"><div class="eyebrow">' + (extraClass ? 'EXTRACTED FROM TALLY' : 'TRANSPARENT BY DESIGN') + '</div>' +
    '<button id="modal-close" aria-label="Close">×</button></div><h2>' + escapeHtml(title) + '</h2>' + body +
    '<button class="primary" id="modal-done">Done</button></div></div>';
  function closeModal() { area.innerHTML = ''; }
  document.getElementById('modal-close').onclick = closeModal;
  document.getElementById('modal-done').onclick = closeModal;
  document.getElementById('modal-overlay').onclick = function (event) {
    if (event.target.id === 'modal-overlay') closeModal();
  };
  document.getElementById('modal-close').focus();
}

function showRequirements() {
  var html = '<p>Flask requests each job page first. Criteria are mapped to text found in the job description; other requirements remain visible for manual review. This is keyword extraction, not a substitute for reading the original.</p><div class="job-grid">';
  ['design', 'lca'].forEach(function (key) {
    var role = roles[key];
    if (!role) return;
    var jd = role.jd || {};
    var label = jobSourceLabel(jd);
    if (jd.fetched_at) label += ' · ' + new Date(jd.fetched_at).toLocaleString();
    html += '<section class="job-card"><h3>' + escapeHtml(jd.title || role.name) + '</h3><p class="' + (jd.source === 'live' ? 'source-live' : 'source-stale') + '">' + escapeHtml(label) + '</p>' +
      '<a href="' + safeLink(role.url) + '" target="_blank" rel="noopener noreferrer">Open original job description ↗</a><h4>Requirements used in scoring</h4>';
    (role.rules || []).forEach(function (rule) {
      html += '<div class="mapped-rule"><b>' + escapeHtml(rule.label) + '<span>' + rule.weight + ' pts</span></b><p>' + escapeHtml((rule.jdEvidence || [])[0] || '') + '</p></div>';
    });
    var requirements = role.requirements || [];
    html += '<details><summary>All extracted requirements (' + requirements.length + ')</summary><ul>';
    requirements.forEach(function (line) { html += '<li>' + escapeHtml(line) + '</li>'; });
    html += '</ul></details></section>';
  });
  html += '</div><p>Location, independence, onsite availability, design quality and other requirements may need a person to verify them. Some requirements are not scored.</p>';
  openModal('Compare job requirements', html, 'jobs-modal');
}

function showMethod() {
  var html = '<p>Flask uses saved Tally descriptions when available and activates matching configured criteria. Each matched criterion earns its listed weight. Partial evidence earns partial points. Scores are normalized to 100; the same rules apply to supplied and uploaded resumes.</p>';
  ['lca', 'design'].forEach(function (key) {
    var role = roles[key];
    if (!role) return;
    html += '<div class="method-role"><h3>' + escapeHtml(role.name) + '</h3>';
    (role.rules || []).forEach(function (rule) { html += '<div><span>' + escapeHtml(rule.label) + '</span><b>' + rule.weight + ' pts</b></div>'; });
    html += '</div>';
  });
  html += '<h3>Classification & ranking</h3><p>Below 30 on both roles is neither / uncertain. If both are at least 30 and differ by less than 15, classify as both; otherwise the higher score is the stronger match. Extraction warnings override this with uncertain. Rankings are descending within the selected role, with filename used to break ties.</p>' +
    '<p>Experience highlights distinguish relevant role tenure from recorded work and training. Overlapping dates are counted once. Dates without a day are approximate; present roles may be stale. Undated duration claims are not added to dated roles because overlap is unknown. LCA experience earns 3 points below 2 years, 6 points for 2 to under 4, and 10 points for 4 or more. UI/UX experience is informational, not scored. Role tenure does not verify relevant duties or independent project work.</p>' +
    '<p>Incomplete ISO identifiers are flagged for review, not scored. Scores are keyword-based indicators, not hiring decisions. Review the original resumes, all requirements, and the difference between the assignment and the linked job description.</p>' +
    '<p><b>Privacy:</b> Saved resumes contain personal data. Use private storage and strong access controls before sharing the app.</p>';
  openModal('How scores work', html, '');
}

// Connect buttons and inputs to the functions above.
uploadButton.onclick = function () { fileInput.click(); };
fileInput.onchange = function () { uploadFiles(fileInput.files); };
document.getElementById('lca-tab').onclick = function () { setRole('lca'); };
document.getElementById('design-tab').onclick = function () { setRole('design'); };
document.getElementById('method-button').onclick = showMethod;
searchInput.oninput = renderList;
filterInput.onchange = renderList;
sortInput.onchange = renderList;
document.getElementById('candidate-list').onclick = function (event) {
  var button = event.target.closest('[data-candidate-id]');
  if (button) selectCandidate(button.getAttribute('data-candidate-id'));
};
document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') document.getElementById('modal-area').innerHTML = '';
});

renderPage();
loadCandidates();
