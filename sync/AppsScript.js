// ============================================================
// Atlanta Ventures Job Board — Google Sheets menu
// Extensions → Apps Script → paste this → Save → run onOpen once to authorize
//
// Fill in WEBHOOK_BASE and WEBHOOK_SECRET before using.
// ============================================================

const WEBHOOK_BASE   = PropertiesService.getScriptProperties().getProperty('WEBHOOK_BASE');
const WEBHOOK_SECRET = PropertiesService.getScriptProperties().getProperty('WEBHOOK_SECRET');

// ---------------------------------------------------------------------------

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Job Board')
    .addItem('Approve job(s) → push to WordPress', 'approveJob')
    .addItem('Add LinkedIn job manually', 'addLinkedInJob')
    .addItem('Remove job(s) from WordPress', 'removeJob')
    .addSeparator()
    .addItem('Run scraper now', 'runScraper')
    .addToUi();
}

// ---------------------------------------------------------------------------
// Approve one or more skipped jobs and post them to WordPress.
// How to use: go to the Skipped tab, select one or more rows, then
// Job Board → Approve job(s).
// ---------------------------------------------------------------------------
function approveJob() {
  const ui    = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActiveSheet();

  if (sheet.getName() !== 'Skipped') {
    ui.alert('Wrong tab', 'Go to the Skipped tab first, then select the row(s) you want to approve.', ui.ButtonSet.OK);
    return;
  }

  const jobs = _selectedJobs(sheet);
  if (!jobs) return;

  const label = jobs.length === 1
    ? `"${jobs[0].title}" (${jobs[0].company})`
    : `${jobs.length} jobs`;

  const confirm = ui.alert('Approve job', `Post ${label} to WordPress?`, ui.ButtonSet.YES_NO);
  if (confirm !== ui.Button.YES) return;

  // Process bottom-to-top so row deletions don't shift the indices of rows
  // we haven't processed yet.
  jobs.sort((a, b) => b.row - a.row);

  let succeeded = 0, failed = 0;
  for (const job of jobs) {
    const result = _post(WEBHOOK_BASE + '/approve-job', {
      company:         job.company,
      job_title:       job.title,
      application_url: job.url,
      row_number:      job.row,
    });
    if (result.ok) succeeded++;
    else failed++;
  }

  if (failed === 0) {
    ui.alert('Done', `${succeeded} job(s) posted to WordPress.`, ui.ButtonSet.OK);
  } else {
    ui.alert('Partial success', `${succeeded} posted, ${failed} failed.`, ui.ButtonSet.OK);
  }
}

// ---------------------------------------------------------------------------
// Add a LinkedIn job directly — no Skipped tab row needed.
// Prompts for company, title, and URL, then calls /approve-job.
// ---------------------------------------------------------------------------
function addLinkedInJob() {
  const ui = SpreadsheetApp.getUi();

  const companyResp = ui.prompt('Add LinkedIn job (1/4)', 'Company name:', ui.ButtonSet.OK_CANCEL);
  if (companyResp.getSelectedButton() !== ui.Button.OK) return;
  const company = companyResp.getResponseText().trim();
  if (!company) { ui.alert('Company name is required.'); return; }

  const titleResp = ui.prompt('Add LinkedIn job (2/4)', 'Job title:', ui.ButtonSet.OK_CANCEL);
  if (titleResp.getSelectedButton() !== ui.Button.OK) return;
  const title = titleResp.getResponseText().trim();
  if (!title) { ui.alert('Job title is required.'); return; }

  const urlResp = ui.prompt('Add LinkedIn job (3/4)', 'LinkedIn job URL:', ui.ButtonSet.OK_CANCEL);
  if (urlResp.getSelectedButton() !== ui.Button.OK) return;
  const url = urlResp.getResponseText().trim();
  if (!url) { ui.alert('URL is required.'); return; }

  const locationResp = ui.prompt('Add LinkedIn job (4/4)', 'Work arrangement (Remote / Hybrid / In Person):', ui.ButtonSet.OK_CANCEL);
  if (locationResp.getSelectedButton() !== ui.Button.OK) return;
  const location = locationResp.getResponseText().trim();
  if (!location) { ui.alert('Work arrangement is required.'); return; }

  const expireResp = ui.alert(
    'Auto-expiration',
    'Automatically remove this job after 30 days?',
    ui.ButtonSet.YES_NO
  );
  const autoExpire = expireResp === ui.Button.YES;

  const confirm = ui.alert(
    'Add LinkedIn job',
    `Post "${title}" at ${company} to WordPress?` + (autoExpire ? '\nWill auto-expire in 30 days.' : ''),
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  const result = _post(WEBHOOK_BASE + '/approve-job', {
    company:         company,
    job_title:       title,
    application_url: url,
    job_location:    location,
    auto_expire:     autoExpire,
  });

  if (result.ok) {
    ui.alert('Done', `"${title}" at ${company} has been posted to WordPress.`, ui.ButtonSet.OK);
  } else {
    try {
      const body = JSON.parse(result.body || '{}');
      if (body.status === 'already_posted') {
        ui.alert('Already posted', 'This job is already on the website.', ui.ButtonSet.OK);
        return;
      }
    } catch (_) {}
    ui.alert('Error', 'Could not post the job: ' + result.body, ui.ButtonSet.OK);
  }
}

// ---------------------------------------------------------------------------
// Remove one or more live jobs from WordPress.
// How to use: go to the Jobs tab, select one or more rows, then
// Job Board → Remove job(s).
// ---------------------------------------------------------------------------
function removeJob() {
  const ui    = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActiveSheet();

  if (sheet.getName() !== 'Jobs') {
    ui.alert('Wrong tab', 'Go to the Jobs tab first, then select the row(s) you want to remove.', ui.ButtonSet.OK);
    return;
  }

  const jobs = _selectedJobs(sheet);
  if (!jobs) return;

  const label = jobs.length === 1
    ? `"${jobs[0].title}" (${jobs[0].company})`
    : `${jobs.length} jobs`;

  const confirm = ui.alert('Remove job', `Remove ${label} from WordPress? This cannot be undone.`, ui.ButtonSet.YES_NO);
  if (confirm !== ui.Button.YES) return;

  let succeeded = 0, failed = 0;
  for (const job of jobs) {
    const result = _post(WEBHOOK_BASE + '/remove-job', {
      company:         job.company,
      job_title:       job.title,
      application_url: job.url,
      row_number:      job.row,
    });
    if (result.ok) succeeded++;
    else failed++;
  }

  if (failed === 0) {
    ui.alert('Done', `${succeeded} job(s) removed from WordPress.`, ui.ButtonSet.OK);
  } else {
    ui.alert('Partial success', `${succeeded} removed, ${failed} failed.`, ui.ButtonSet.OK);
  }
}

// ---------------------------------------------------------------------------
// Manually kick off a full scrape + WordPress sync.
// ---------------------------------------------------------------------------
function runScraper() {
  const ui = SpreadsheetApp.getUi();

  const confirm = ui.alert(
    'Run scraper',
    'This will scrape all companies and sync results to WordPress. It runs in the background — check the sheet in a few minutes for updates. Continue?',
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  const result = _post(WEBHOOK_BASE + '/run', {});

  if (result.ok) {
    ui.alert('Started', 'Scraper is running. New jobs will appear in the Jobs tab when it finishes.', ui.ButtonSet.OK);
  } else {
    ui.alert('Error', 'Could not start the scraper: ' + result.body, ui.ButtonSet.OK);
  }
}

// ---------------------------------------------------------------------------
// Read job data from every selected row (skipping the header).
// Returns an array of {company, title, url, row}, or null if nothing valid selected.
// ---------------------------------------------------------------------------
function _selectedJobs(sheet) {
  const ui    = SpreadsheetApp.getUi();
  const range = sheet.getActiveRange();
  const startRow = range.getRow();
  const numRows  = range.getNumRows();

  const jobs = [];
  for (let i = 0; i < numRows; i++) {
    const row = startRow + i;
    if (row <= 1) continue;
    const values  = sheet.getRange(row, 1, 1, 3).getValues()[0];
    const company = String(values[0] || '').trim();
    const title   = String(values[1] || '').trim();
    const url     = String(values[2] || '').trim();
    if (company && title) jobs.push({ company, title, url, row });
  }

  if (jobs.length === 0) {
    ui.alert('No valid job rows selected. Select at least one data row (not the header).');
    return null;
  }
  return jobs;
}

// ---------------------------------------------------------------------------
// Internal helper — POST JSON to a webhook endpoint.
// ---------------------------------------------------------------------------
function _post(url, payload) {
  try {
    const resp = UrlFetchApp.fetch(url, {
      method:             'post',
      contentType:        'application/json',
      payload:            JSON.stringify(payload),
      headers:            { 'X-Secret': WEBHOOK_SECRET },
      muteHttpExceptions: true,
    });
    const code = resp.getResponseCode();
    return { ok: code >= 200 && code < 300, body: resp.getContentText() };
  } catch (e) {
    return { ok: false, body: e.toString() };
  }
}
