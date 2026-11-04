// Runs the real .gs files in a Node sandbox with fake Google services.
// Usage (from apps_script/):  node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SECRET = 'x'.repeat(40);

function iterator(items) {
  let i = 0;
  return { hasNext: () => i < items.length, next: () => items[i++] };
}

function makeAttachment(name, size) {
  return { getName: () => name, getSize: () => size, getContentType: () => 'application/octet-stream', copyBlob: () => ({ name }) };
}

function loadBridge(overrides = {}) {
  const created = [];
  const labelCalls = [];
  const message = {
    getId: () => 'm1', getFrom: () => 'alice@example.com', getTo: () => 'me@example.com', getCc: () => '',
    getSubject: () => 'Invoices', getDate: () => new Date('2026-11-09T07:00:00Z'), getPlainBody: () => 'Body text',
    isUnread: () => true, getAttachments: () => [makeAttachment('a.pdf', 1000), makeAttachment('big.pdf', 30 * 1048576), makeAttachment('run.exe', 10), makeAttachment('dup.pdf', 10)],
    getThread: () => ({ getId: () => 't1', getLabels: () => [] }),
    markRead: () => labelCalls.push('markRead'), markUnread: () => labelCalls.push('markUnread'),
  };
  const folder = {
    getId: () => 'folder1', getName: () => 'Email Attachments', getUrl: () => 'https://drive/folder1',
    getLastUpdated: () => new Date('2026-11-01T00:00:00Z'),
    getFilesByName: (name) => iterator(name === 'dup.pdf' ? [{}] : []),
    createFile: (blob) => { created.push(blob.name); return { getName: () => blob.name, getId: () => 'f-' + blob.name, getUrl: () => 'u' }; },
  };
  const context = {
    console,
    ContentService: {
      MimeType: { JSON: 'json' },
      createTextOutput: (text) => ({ text, setMimeType() { return this; } }),
    },
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => overrides.secret === undefined ? SECRET : overrides.secret }) },
    Utilities: {
      formatDate: (date) => date.toISOString().replace(/\.\d{3}Z$/, 'Z'),
      getUuid: () => '00000000-0000-0000-0000-000000000000',
    },
    Session: { getEffectiveUser: () => ({ getEmail: () => 'me@example.com' }), getScriptTimeZone: () => 'Etc/UTC' },
    MimeType: { FOLDER: 'application/vnd.google-apps.folder' },
    GmailApp: {
      search: (query, start, max) => [{ getMessages: () => [message], getId: () => 't1', isUnread: () => true, getMessageCount: () => 1 }],
      getMessageById: (id) => (id === 'm1' ? message : null),
      getUserLabelByName: (name) => (name === 'Work' ? { addToThread: () => labelCalls.push('add:Work'), removeFromThread: () => labelCalls.push('remove:Work') } : null),
      getUserLabels: () => [],
      sendEmail: (...args) => { labelCalls.push(['sendEmail', ...args]); },
    },
    CalendarApp: {
      getDefaultCalendar: () => ({
        getTimeZone: () => 'Etc/UTC',
        getEventById: (id) => (id === 'rec1' ? { isRecurringEvent: () => true } : null),
      }),
    },
    DriveApp: {
      searchFiles: (query) => { context.lastQuery = query; return iterator([]); },
      getRootFolder: () => ({ getFoldersByName: () => iterator([folder]), createFolder: () => folder }),
    },
    Logger: { log: () => {} },
  };
  vm.createContext(context);
  for (const file of ['Response.gs', 'Validation.gs', 'Gmail.gs', 'Calendar.gs', 'Drive.gs', 'Code.gs']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), context, { filename: file });
  }
  const post = (body) => JSON.parse(context.doPost({ postData: { contents: JSON.stringify(body) } }).text);
  return { context, post, created, labelCalls };
}

test('rejects a wrong or missing secret', () => {
  const { post } = loadBridge();
  assert.strictEqual(post({ secret: 'nope', operation: 'ping', params: {} }).error.code, 'unauthorized');
  assert.strictEqual(post({ operation: 'ping', params: {} }).error.code, 'unauthorized');
});

test('refuses to run without a configured secret', () => {
  const { post } = loadBridge({ secret: null });
  assert.strictEqual(post({ secret: SECRET, operation: 'ping' }).error.code, 'not_configured');
});

test('rejects unknown operations, including prototype names', () => {
  const { post } = loadBridge();
  assert.strictEqual(post({ secret: SECRET, operation: 'gmail.deleteAll' }).error.code, 'unknown_operation');
  assert.strictEqual(post({ secret: SECRET, operation: 'constructor' }).error.code, 'unknown_operation');
});

test('rejects malformed bodies', () => {
  const { context } = loadBridge();
  const result = JSON.parse(context.doPost({ postData: { contents: '{not json' } }).text);
  assert.strictEqual(result.error.code, 'bad_request');
  assert.strictEqual(JSON.parse(context.doPost({}).text).error.code, 'bad_request');
});

test('doGet does not expose any data', () => {
  const { context } = loadBridge();
  assert.strictEqual(JSON.parse(context.doGet().text).ok, false);
});

test('ping and gmail.search return formatted data', () => {
  const { post } = loadBridge();
  assert.strictEqual(post({ secret: SECRET, operation: 'ping' }).data.account, 'me@example.com');
  const search = post({ secret: SECRET, operation: 'gmail.search', params: { query: 'is:unread', max_results: 5 } });
  assert.strictEqual(search.ok, true);
  assert.deepStrictEqual(Object.keys(search.data.messages[0]).sort(),
    ['date', 'from', 'id', 'message_count', 'snippet', 'subject', 'thread_id', 'to', 'unread']);
  assert.strictEqual(search.data.messages[0].date, '2026-11-09T07:00:00Z');
});

test('validates parameters', () => {
  const { post } = loadBridge();
  assert.strictEqual(post({ secret: SECRET, operation: 'gmail.search', params: { query: 'x', max_results: 999 } }).error.code, 'invalid_params');
  assert.strictEqual(post({ secret: SECRET, operation: 'gmail.send', params: { to: ['bad'], subject: 's', body: 'b' } }).error.code, 'invalid_params');
  assert.strictEqual(post({ secret: SECRET, operation: 'calendar.listEvents', params: { start: '2026-11-10T00:00:00', end: '2026-11-11T00:00:00Z' } }).error.code, 'invalid_params');
  assert.strictEqual(post({ secret: SECRET, operation: 'gmail.read', params: { message_id: '../x' } }).error.code, 'invalid_params');
});

test('gmail.read truncates the body and reports not found', () => {
  const { post } = loadBridge();
  const read = post({ secret: SECRET, operation: 'gmail.read', params: { message_id: 'm1', max_body_chars: 4 } });
  assert.strictEqual(read.data.body, 'Body');
  assert.strictEqual(read.data.body_truncated, true);
  assert.strictEqual(read.data.attachments.length, 4);
  assert.strictEqual(post({ secret: SECRET, operation: 'gmail.read', params: { message_id: 'm2' } }).error.code, 'not_found');
});

test('label changes do nothing if any label is missing', () => {
  const { post, labelCalls } = loadBridge();
  const result = post({ secret: SECRET, operation: 'gmail.modifyLabels', params: { message_id: 'm1', add_labels: ['Work', 'Missing'], mark_read: true } });
  assert.strictEqual(result.error.code, 'not_found');
  assert.deepStrictEqual(labelCalls, []);
});

test('forward with a note sends a plain-text forward with attachments', () => {
  const { post, labelCalls } = loadBridge();
  const result = post({ secret: SECRET, operation: 'gmail.forward', params: { message_id: 'm1', to: ['bob@example.com'], note: 'FYI' } });
  assert.strictEqual(result.ok, true);
  const [name, to, subject, body, options] = labelCalls[0];
  assert.strictEqual(name, 'sendEmail');
  assert.strictEqual(to, 'bob@example.com');
  assert.strictEqual(subject, 'Fwd: Invoices');
  assert.ok(body.startsWith('FYI\n\n---------- Forwarded message'));
  assert.strictEqual(options.attachments.length, 4);
});

test('recurring events cannot be changed or cancelled', () => {
  const { post } = loadBridge();
  for (const operation of ['calendar.updateEvent', 'calendar.cancelEvent']) {
    assert.strictEqual(post({ secret: SECRET, operation, params: { event_id: 'rec1', title: 'x' } }).error.code, 'unsupported');
  }
});

test('drive search builds an escaped query from structured filters', () => {
  const { post, context } = loadBridge();
  post({ secret: SECRET, operation: 'drive.search', params: { name_contains: "O'Brien \\ report", modified_after: '2026-01-01' } });
  assert.strictEqual(context.lastQuery,
    "trashed = false and title contains 'O\\'Brien \\\\ report' and modifiedDate >= '2026-01-01T00:00:00'");
  assert.strictEqual(post({ secret: SECRET, operation: 'drive.search', params: {} }).error.code, 'invalid_params');
  assert.strictEqual(post({ secret: SECRET, operation: 'drive.search', params: { modified_after: "2026' or '1'='1" } }).error.code, 'invalid_params');
});

test('saveAttachments skips oversized, unsupported and duplicate files', () => {
  const { post, created } = loadBridge();
  const result = post({ secret: SECRET, operation: 'drive.saveAttachments', params: {
    message_id: 'm1', folder_name: 'Email Attachments', max_bytes: 10 * 1048576, allowed_extensions: ['pdf'],
  } });
  assert.strictEqual(result.ok, true, JSON.stringify(result));
  assert.deepStrictEqual(created, ['a.pdf']);
  const reasons = Object.fromEntries(result.data.skipped.map((s) => [s.name, s.reason]));
  assert.match(reasons['big.pdf'], /Too large/);
  assert.match(reasons['run.exe'], /not supported/);
  assert.match(reasons['dup.pdf'], /already in the folder/);
});

test('saveAttachments enforces the hard size cap', () => {
  const { post } = loadBridge();
  const result = post({ secret: SECRET, operation: 'drive.saveAttachments', params: {
    message_id: 'm1', folder_name: 'x', max_bytes: 100 * 1048576, allowed_extensions: ['pdf'],
  } });
  assert.strictEqual(result.error.code, 'invalid_params');
});

test('there is no delete or trash operation', () => {
  const { context } = loadBridge();
  const names = vm.runInContext('Object.keys(operations_())', context);
  assert.ok(names.every((n) => !/delete|trash|remove(?!Labels)/i.test(n)), names.join(','));
  const sources = ['Gmail.gs', 'Drive.gs'].map((f) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8')).join('\n');
  assert.ok(!/moveToTrash|setTrashed|\.remove\(|Drive\.Files\.remove/.test(sources));
});
