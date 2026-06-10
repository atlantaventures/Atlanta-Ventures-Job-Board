// ============================================================
// Atlanta Ventures Job Board — Google Sheets menu
// Extensions → Apps Script → paste this → Save → run onOpen once to authorize
//
// Fill in WEBHOOK_BASE and WEBHOOK_SECRET before using.
// WEBHOOK_BASE should be your Digital Ocean server IP or domain, e.g.:
//   const WEBHOOK_BASE = 'http://123.456.78.90:5001';
// ============================================================

const WEBHOOK_BASE   = 'YOUR_DO_IP_OR_DOMAIN';  // e.g. 'http://123.456.78.90:5001'
const WEBHOOK_SECRET = 'YOUR_WEBHOOK_SECRET';    // must match WEBHOOK_SECRET in .env on the server

// ---------------------------------------------------------------------------

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Job Board')
    .addItem('Approve job → push to WordPress', 'approveJob')
    .addItem('Remove job from WordPress', 'removeJob')
    .addSeparator()
    .addItem('Run scraper now', 'runScraper')
    .addToUi();
}

// ---------------------------------------------------------------------------
// Approve a skipped job and post it to WordPress.
// How to use: go to the Skipped tab, click any cell in the row you want, then
// Job Board → Approve job.
// ---------------------------------------------------------------------------
function approveJob() {
  const ui    = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActiveSheet();

  if (sheet.getName() !== 'Skipped') {
    ui.alert('Wrong tab', 'Go to the Skipped tab first, then click the row you want to approve.', ui.ButtonSet.OK);
    return;
  }

  const row = sheet.getActiveRange().getRow();
  if (row <= 1) {
    ui.alert('Select a job row (not the header).');
    return;
  }

  const company = sheet.getRange(row, 1).getValue();
  const title   = sheet.getRange(row, 2).getValue();
  const url     = sheet.getRange(row, 3).getValue();

  if (!company || !title) {
    ui.alert('Could not read job data from this row. Make sure a job row is selected.');
    return;
  }

  const confirm = ui.alert(
    'Approve job',
    `Post "${title}" (${company}) to WordPress?`,
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  const result = _post(WEBHOOK_BASE + '/approve-job', {
    company:         company,
    job_title:       title,
    application_url: url,
    row_number:      row,
  });

  if (result.ok) {
    ui.alert('Done', `"${title}" is now live on the job board.`, ui.ButtonSet.OK);
  } else {
    ui.alert('Error', 'Something went wrong: ' + result.body, ui.ButtonSet.OK);
  }
}

// ---------------------------------------------------------------------------
// Remove a live job from WordPress.
// How to use: go to the Jobs tab, click any cell in the row you want to remove,
// then Job Board → Remove job.
// ---------------------------------------------------------------------------
function removeJob() {
  const ui    = SpreadsheetApp.getUi();
  const sheet = SpreadsheetApp.getActiveSheet();

  if (sheet.getName() !== 'Jobs') {
    ui.alert('Wrong tab', 'Go to the Jobs tab first, then click the row you want to remove.', ui.ButtonSet.OK);
    return;
  }

  const row = sheet.getActiveRange().getRow();
  if (row <= 1) {
    ui.alert('Select a job row (not the header).');
    return;
  }

  const company = sheet.getRange(row, 1).getValue();
  const title   = sheet.getRange(row, 2).getValue();
  const url     = sheet.getRange(row, 3).getValue();

  if (!company || !title) {
    ui.alert('Could not read job data from this row. Make sure a job row is selected.');
    return;
  }

  const confirm = ui.alert(
    'Remove job',
    `Remove "${title}" (${company}) from WordPress? This cannot be undone.`,
    ui.ButtonSet.YES_NO
  );
  if (confirm !== ui.Button.YES) return;

  const result = _post(WEBHOOK_BASE + '/remove-job', {
    company:         company,
    job_title:       title,
    application_url: url,
    row_number:      row,
  });

  if (result.ok) {
    ui.alert('Done', `"${title}" has been removed from the job board.`, ui.ButtonSet.OK);
  } else {
    ui.alert('Error', 'Something went wrong: ' + result.body, ui.ButtonSet.OK);
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
// Internal helper — POST JSON to a webhook endpoint.
// ---------------------------------------------------------------------------
function _post(url, payload) {
  try {
    const resp = UrlFetchApp.fetch(url, {
      method:           'post',
      contentType:      'application/json',
      payload:          JSON.stringify(payload),
      headers:          { 'X-Secret': WEBHOOK_SECRET },
      muteHttpExceptions: true,
    });
    const code = resp.getResponseCode();
    return { ok: code >= 200 && code < 300, body: resp.getContentText() };
  } catch (e) {
    return { ok: false, body: e.toString() };
  }
}
