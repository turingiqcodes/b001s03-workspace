/** Response formatting and error helpers. */

function jsonResponse_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload)).setMimeType(ContentService.MimeType.JSON);
}

/** An Error with a short machine-readable code. Its message is shown to the user. */
function bridgeError_(code, message) {
  var err = new Error(message);
  err.bridgeCode = code;
  return err;
}

function errorPayload_(err) {
  if (err && err.bridgeCode) {
    return { ok: false, error: { code: err.bridgeCode, message: err.message } };
  }
  // Errors thrown by Google services (quota, permissions, ...). Keep the message short.
  var message = err && err.message ? String(err.message).slice(0, 300) : 'Unknown error';
  return { ok: false, error: { code: 'google_error', message: 'Google reported an error: ' + message } };
}

/** ISO 8601 in UTC with a trailing Z, e.g. 2026-11-10T09:30:00Z. */
function isoUtc_(date) {
  return Utilities.formatDate(date, 'UTC', "yyyy-MM-dd'T'HH:mm:ss'Z'");
}
