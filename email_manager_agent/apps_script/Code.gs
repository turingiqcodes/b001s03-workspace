/**
 * Email Manager Agent - Google Apps Script bridge.
 *
 * Deployed as a Web App that runs as the account owner. The local FastAPI backend
 * POSTs JSON to it:
 *
 *   {"secret": "...", "operation": "gmail.search", "params": {...}}
 *
 * and gets back {"ok": true, "data": ...} or {"ok": false, "error": {"code", "message"}}.
 *
 * The bridge only does what the backend asks after the backend's own validation and
 * the user's approval. It re-validates every input anyway, and it has no delete
 * operations for email or files.
 */

/**
 * Operation name -> handler. Built on first use, not at load time: Apps Script runs each
 * file's top-level code in file order, so a table built here would reference handlers
 * in Gmail.gs, Calendar.gs and Drive.gs before those files have loaded.
 */
function operations_() {
  return {
  'ping': ping_,
  'gmail.search': gmailSearch_,
  'gmail.read': gmailRead_,
  'gmail.listLabels': gmailListLabels_,
  'gmail.createDraft': gmailCreateDraft_,
  'gmail.send': gmailSend_,
  'gmail.reply': gmailReply_,
  'gmail.createReplyDraft': gmailCreateReplyDraft_,
  'gmail.forward': gmailForward_,
  'gmail.modifyLabels': gmailModifyLabels_,
  'calendar.listEvents': calendarListEvents_,
  'calendar.getEvent': calendarGetEvent_,
  'calendar.createEvent': calendarCreateEvent_,
  'calendar.updateEvent': calendarUpdateEvent_,
  'calendar.cancelEvent': calendarCancelEvent_,
  'drive.search': driveSearch_,
  'drive.list': driveList_,
  'drive.getFile': driveGetFile_,
  'drive.createFolder': driveCreateFolder_,
  'drive.moveFile': driveMoveFile_,
  'drive.saveAttachments': driveSaveAttachments_
  };
}

function doPost(e) {
  try {
    var request = parseRequest_(e);
    checkSecret_(request.secret);
    var operations = operations_();
    var handler = operations.hasOwnProperty(request.operation) ? operations[request.operation] : null;
    if (!handler) {
      throw bridgeError_('unknown_operation', 'Unsupported operation: ' + String(request.operation).slice(0, 60));
    }
    var data = handler(request.params || {});
    return jsonResponse_({ ok: true, data: data });
  } catch (err) {
    return jsonResponse_(errorPayload_(err));
  }
}

function doGet() {
  return jsonResponse_({
    ok: false,
    error: { code: 'method_not_allowed', message: 'Use POST. This endpoint is for the Email Manager Agent backend.' }
  });
}

function ping_() {
  return {
    account: Session.getEffectiveUser().getEmail(),
    script_timezone: Session.getScriptTimeZone(),
    calendar_timezone: CalendarApp.getDefaultCalendar().getTimeZone()
  };
}

/**
 * Run from the Apps Script editor after setup. It authorizes the project's scopes and
 * reports whether the shared secret is configured, without printing it.
 *
 * The secret lives in the backend's .env (APPS_SCRIPT_SHARED_SECRET). Copy the same value
 * into Project Settings > Script properties as BRIDGE_SHARED_SECRET. It is never
 * written into this code.
 */
function checkSetup() {
  var secret = PropertiesService.getScriptProperties().getProperty('BRIDGE_SHARED_SECRET');
  var info = ping_();
  if (!secret || secret.length < 32) {
    Logger.log('BRIDGE_SHARED_SECRET is missing or shorter than 32 characters. Add the value of ' +
      'APPS_SCRIPT_SHARED_SECRET from .env in Project Settings > Script properties.');
  } else {
    Logger.log('BRIDGE_SHARED_SECRET is set (' + secret.length + ' characters).');
  }
  Logger.log('Running as ' + info.account + ', calendar time zone ' + info.calendar_timezone + '.');
}
