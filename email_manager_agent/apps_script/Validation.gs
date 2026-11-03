/** Request parsing and input validation. Every operation validates its own params with these. */

var EMAIL_PATTERN_ = /^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$/;
var ID_PATTERN_ = /^[A-Za-z0-9_\-@.]{1,256}$/;
var MAX_ATTACHMENT_BYTES_HARD_LIMIT_ = 25 * 1024 * 1024;

function parseRequest_(e) {
  if (!e || !e.postData || !e.postData.contents) {
    throw bridgeError_('bad_request', 'Missing request body.');
  }
  var request;
  try {
    request = JSON.parse(e.postData.contents);
  } catch (parseError) {
    throw bridgeError_('bad_request', 'Request body is not valid JSON.');
  }
  if (!request || typeof request !== 'object' || typeof request.operation !== 'string') {
    throw bridgeError_('bad_request', 'Request must be an object with an operation.');
  }
  if (request.params !== undefined && (typeof request.params !== 'object' || request.params === null)) {
    throw bridgeError_('bad_request', 'params must be an object.');
  }
  return request;
}

function checkSecret_(given) {
  var expected = PropertiesService.getScriptProperties().getProperty('BRIDGE_SHARED_SECRET');
  if (!expected || expected.length < 32) {
    throw bridgeError_('not_configured', 'The bridge has no shared secret. Run setupSharedSecret() in the Apps Script editor.');
  }
  if (typeof given !== 'string' || !constantTimeEquals_(given, expected)) {
    throw bridgeError_('unauthorized', 'Invalid shared secret.');
  }
}

/** Compare without stopping at the first different character. */
function constantTimeEquals_(a, b) {
  if (a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

function requireString_(params, name, options) {
  options = options || {};
  var value = params[name];
  if (value === undefined || value === null || value === '') {
    if (options.optional) return options.defaultValue !== undefined ? options.defaultValue : '';
    throw bridgeError_('invalid_params', name + ' is required.');
  }
  if (typeof value !== 'string') throw bridgeError_('invalid_params', name + ' must be a string.');
  if (options.max !== undefined && value.length > options.max) {
    throw bridgeError_('invalid_params', name + ' is longer than ' + options.max + ' characters.');
  }
  return value;
}

function requireInt_(params, name, min, max, defaultValue) {
  var value = params[name];
  if (value === undefined || value === null) {
    if (defaultValue !== undefined) return defaultValue;
    throw bridgeError_('invalid_params', name + ' is required.');
  }
  if (typeof value !== 'number' || Math.floor(value) !== value || value < min || value > max) {
    throw bridgeError_('invalid_params', name + ' must be a whole number from ' + min + ' to ' + max + '.');
  }
  return value;
}

function requireBool_(params, name, defaultValue) {
  var value = params[name];
  if (value === undefined || value === null) return defaultValue;
  if (typeof value !== 'boolean') throw bridgeError_('invalid_params', name + ' must be true or false.');
  return value;
}

function requireId_(params, name, optional) {
  var value = params[name];
  if ((value === undefined || value === null || value === '') && optional) return null;
  if (typeof value !== 'string' || !ID_PATTERN_.test(value)) {
    throw bridgeError_('invalid_params', name + ' is not a valid id.');
  }
  return value;
}

function requireEmailList_(params, name, optional) {
  var value = params[name];
  if (value === undefined || value === null) value = [];
  if (!Array.isArray(value)) throw bridgeError_('invalid_params', name + ' must be a list of email addresses.');
  if (!optional && value.length === 0) throw bridgeError_('invalid_params', name + ' needs at least one address.');
  for (var i = 0; i < value.length; i++) {
    if (typeof value[i] !== 'string' || !EMAIL_PATTERN_.test(value[i])) {
      throw bridgeError_('invalid_params', name + ' contains an invalid email address.');
    }
  }
  return value;
}

function requireStringList_(params, name) {
  var value = params[name];
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw bridgeError_('invalid_params', name + ' must be a list.');
  for (var i = 0; i < value.length; i++) {
    if (typeof value[i] !== 'string' || value[i].length === 0 || value[i].length > 200) {
      throw bridgeError_('invalid_params', name + ' contains an invalid entry.');
    }
  }
  return value;
}

/** Parse an ISO 8601 date-time that includes a UTC offset or Z. */
function requireDate_(params, name, optional) {
  var value = params[name];
  if ((value === undefined || value === null) && optional) return null;
  if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) {
    throw bridgeError_('invalid_params', name + ' must be an ISO 8601 time with a UTC offset.');
  }
  var date = new Date(value);
  if (isNaN(date.getTime())) throw bridgeError_('invalid_params', name + ' is not a valid date.');
  return date;
}
