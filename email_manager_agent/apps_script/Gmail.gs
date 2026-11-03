/** Gmail operations. GmailApp labels apply to whole threads, as in the Gmail UI. */

var GMAIL_MAX_SEARCH_RESULTS_ = 50;
var GMAIL_SNIPPET_CHARS_ = 200;

function getMessage_(messageId) {
  var message = null;
  try {
    message = GmailApp.getMessageById(messageId);
  } catch (err) {
    message = null;
  }
  if (!message) throw bridgeError_('not_found', 'Email not found: ' + messageId);
  return message;
}

function snippet_(text) {
  return String(text || '').replace(/\s+/g, ' ').trim().slice(0, GMAIL_SNIPPET_CHARS_);
}

function gmailSearch_(params) {
  var query = requireString_(params, 'query', { max: 500 });
  var max = requireInt_(params, 'max_results', 1, GMAIL_MAX_SEARCH_RESULTS_, 10);
  var threads = GmailApp.search(query, 0, max);
  var messages = threads.map(function (thread) {
    var threadMessages = thread.getMessages();
    var latest = threadMessages[threadMessages.length - 1];
    return {
      id: latest.getId(),
      thread_id: thread.getId(),
      from: latest.getFrom(),
      to: latest.getTo(),
      subject: latest.getSubject(),
      date: isoUtc_(latest.getDate()),
      snippet: snippet_(latest.getPlainBody()),
      unread: thread.isUnread(),
      message_count: thread.getMessageCount()
    };
  });
  return { messages: messages };
}

function gmailRead_(params) {
  var message = getMessage_(requireId_(params, 'message_id'));
  var maxChars = requireInt_(params, 'max_body_chars', 0, 100000, 10000);
  var body = message.getPlainBody() || '';
  var attachments = message.getAttachments().map(function (attachment, index) {
    return {
      index: index,
      name: attachment.getName(),
      size: attachment.getSize(),
      content_type: attachment.getContentType()
    };
  });
  return {
    id: message.getId(),
    thread_id: message.getThread().getId(),
    from: message.getFrom(),
    to: message.getTo(),
    cc: message.getCc(),
    subject: message.getSubject(),
    date: isoUtc_(message.getDate()),
    unread: message.isUnread(),
    labels: message.getThread().getLabels().map(function (label) { return label.getName(); }),
    body: body.slice(0, maxChars),
    body_truncated: body.length > maxChars,
    attachments: attachments
  };
}

function gmailListLabels_() {
  return { labels: GmailApp.getUserLabels().map(function (label) { return label.getName(); }) };
}

function composeParams_(params) {
  return {
    to: requireEmailList_(params, 'to', false),
    cc: requireEmailList_(params, 'cc', true),
    bcc: requireEmailList_(params, 'bcc', true),
    subject: requireString_(params, 'subject', { max: 250 }),
    body: requireString_(params, 'body', { max: 50000 })
  };
}

function composeOptions_(email) {
  var options = {};
  if (email.cc.length) options.cc = email.cc.join(',');
  if (email.bcc.length) options.bcc = email.bcc.join(',');
  return options;
}

function gmailCreateDraft_(params) {
  var email = composeParams_(params);
  var draft = GmailApp.createDraft(email.to.join(','), email.subject, email.body, composeOptions_(email));
  return { draft_id: draft.getId(), message_id: draft.getMessage().getId() };
}

function gmailSend_(params) {
  var email = composeParams_(params);
  GmailApp.sendEmail(email.to.join(','), email.subject, email.body, composeOptions_(email));
  return { sent: true, to: email.to, subject: email.subject };
}

function gmailReply_(params) {
  var message = getMessage_(requireId_(params, 'message_id'));
  var body = requireString_(params, 'body', { max: 50000 });
  if (requireBool_(params, 'reply_all', false)) {
    message.replyAll(body);
  } else {
    message.reply(body);
  }
  return { sent: true, thread_id: message.getThread().getId(), subject: message.getSubject() };
}

function gmailCreateReplyDraft_(params) {
  var message = getMessage_(requireId_(params, 'message_id'));
  var body = requireString_(params, 'body', { max: 50000 });
  var draft = requireBool_(params, 'reply_all', false)
    ? message.createDraftReplyAll(body)
    : message.createDraftReply(body);
  return { draft_id: draft.getId(), thread_id: message.getThread().getId() };
}

function gmailForward_(params) {
  var message = getMessage_(requireId_(params, 'message_id'));
  var to = requireEmailList_(params, 'to', false);
  var note = requireString_(params, 'note', { max: 50000, optional: true });
  if (!note) {
    message.forward(to.join(','));
  } else {
    // GmailMessage.forward() has no plain-text note option, so build the forward ourselves.
    var body = note + '\n\n---------- Forwarded message ----------\n' +
      'From: ' + message.getFrom() + '\n' +
      'Date: ' + message.getDate() + '\n' +
      'Subject: ' + message.getSubject() + '\n' +
      'To: ' + message.getTo() + '\n\n' +
      message.getPlainBody();
    GmailApp.sendEmail(to.join(','), 'Fwd: ' + message.getSubject(), body, { attachments: message.getAttachments() });
  }
  return { sent: true, to: to, subject: message.getSubject() };
}

function gmailModifyLabels_(params) {
  var message = getMessage_(requireId_(params, 'message_id'));
  var addNames = requireStringList_(params, 'add_labels');
  var removeNames = requireStringList_(params, 'remove_labels');
  var markRead = params.mark_read === undefined ? null : params.mark_read;
  if (markRead !== null && typeof markRead !== 'boolean') {
    throw bridgeError_('invalid_params', 'mark_read must be true, false or null.');
  }
  var thread = message.getThread();
  var findLabel = function (name) {
    var label = GmailApp.getUserLabelByName(name);
    if (!label) throw bridgeError_('not_found', 'Label not found: ' + name);
    return label;
  };
  // Look up every label first so nothing changes if one is missing.
  var toAdd = addNames.map(findLabel);
  var toRemove = removeNames.map(findLabel);
  toAdd.forEach(function (label) { label.addToThread(thread); });
  toRemove.forEach(function (label) { label.removeFromThread(thread); });
  if (markRead === true) message.markRead();
  if (markRead === false) message.markUnread();
  return { updated: true, added: addNames, removed: removeNames, mark_read: markRead };
}
