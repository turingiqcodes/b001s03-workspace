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

var OPERATIONS_ = {
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

function doPost(e) {
  try {
    var request = parseRequest_(e);
    checkSecret_(request.secret);
    var handler = OPERATIONS_.hasOwnProperty(request.operation) ? OPERATIONS_[request.operation] : null;
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
 * Run once from the Apps Script editor to create a random shared secret.
 * Copy the value from the execution log into APPS_SCRIPT_SHARED_SECRET in .env.
 */
function setupSharedSecret() {
  var secret = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
  PropertiesService.getScriptProperties().setProperty('BRIDGE_SHARED_SECRET', secret);
  Logger.log('BRIDGE_SHARED_SECRET set. Put this in your .env as APPS_SCRIPT_SHARED_SECRET:\n' + secret);
}
