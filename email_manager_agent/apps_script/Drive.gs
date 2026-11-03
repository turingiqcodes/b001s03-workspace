/** Drive operations. There is deliberately no delete, trash or permission-sharing operation. */

var DRIVE_MAX_RESULTS_ = 100;

/** Escape a value for a Drive search query string literal. */
function driveQueryLiteral_(value) {
  return "'" + String(value).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";
}

/** Build a DriveApp.searchFiles query (Drive API v2 syntax) from structured filters only. */
function buildDriveQuery_(params) {
  var clauses = ['trashed = false'];
  var name = requireString_(params, 'name_contains', { max: 200, optional: true });
  var text = requireString_(params, 'full_text', { max: 200, optional: true });
  var mimeType = requireString_(params, 'mime_type', { max: 100, optional: true });
  var modifiedAfter = requireString_(params, 'modified_after', { max: 10, optional: true });
  if (name) clauses.push('title contains ' + driveQueryLiteral_(name));
  if (text) clauses.push('fullText contains ' + driveQueryLiteral_(text));
  if (mimeType) clauses.push('mimeType = ' + driveQueryLiteral_(mimeType));
  if (modifiedAfter) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(modifiedAfter)) {
      throw bridgeError_('invalid_params', 'modified_after must be YYYY-MM-DD.');
    }
    clauses.push("modifiedDate >= '" + modifiedAfter + "T00:00:00'");
  }
  if (clauses.length === 1) throw bridgeError_('invalid_params', 'Give at least one search filter.');
  return clauses.join(' and ');
}

function formatFile_(file) {
  return {
    id: file.getId(),
    name: file.getName(),
    mime_type: file.getMimeType(),
    size: file.getSize(),
    url: file.getUrl(),
    modified: isoUtc_(file.getLastUpdated()),
    is_folder: false
  };
}

function formatFolder_(folder) {
  return {
    id: folder.getId(),
    name: folder.getName(),
    url: folder.getUrl(),
    modified: isoUtc_(folder.getLastUpdated()),
    is_folder: true
  };
}

function collect_(iterator, max, format) {
  var items = [];
  while (iterator.hasNext() && items.length < max) items.push(format(iterator.next()));
  return items;
}

function getFolder_(folderId) {
  try {
    return DriveApp.getFolderById(folderId);
  } catch (err) {
    throw bridgeError_('not_found', 'Drive folder not found: ' + folderId);
  }
}

function getFile_(fileId) {
  try {
    return DriveApp.getFileById(fileId);
  } catch (err) {
    throw bridgeError_('not_found', 'Drive file not found: ' + fileId);
  }
}

function driveSearch_(params) {
  var max = requireInt_(params, 'max_results', 1, DRIVE_MAX_RESULTS_, 10);
  return { files: collect_(DriveApp.searchFiles(buildDriveQuery_(params)), max, formatFile_) };
}

function driveList_(params) {
  var folderId = requireId_(params, 'folder_id', true);
  var max = requireInt_(params, 'max_results', 1, DRIVE_MAX_RESULTS_, 25);
  var folder = folderId ? getFolder_(folderId) : DriveApp.getRootFolder();
  return {
    folder: formatFolder_(folder),
    folders: collect_(folder.getFolders(), max, formatFolder_),
    files: collect_(folder.getFiles(), max, formatFile_)
  };
}

function driveGetFile_(params) {
  var id = requireId_(params, 'file_id');
  var file = null;
  try {
    file = DriveApp.getFileById(id);
  } catch (fileError) {
    file = null;  // not a file id: it may be a folder
  }
  if (file && file.getMimeType() !== MimeType.FOLDER) return formatFile_(file);
  return formatFolder_(getFolder_(id));
}

/** Return the folder with this name inside parent, creating it only if it does not exist. */
function findOrCreateFolder_(parent, name) {
  var existing = parent.getFoldersByName(name);
  if (existing.hasNext()) return { folder: existing.next(), created: false };
  return { folder: parent.createFolder(name), created: true };
}

function checkDriveName_(name) {
  if (/[\/\\<>:"|?*\u0000]/.test(name)) {
    throw bridgeError_('invalid_params', 'Name contains characters that are not allowed.');
  }
  return name;
}

function driveCreateFolder_(params) {
  var name = checkDriveName_(requireString_(params, 'name', { max: 200 }));
  var parentId = requireId_(params, 'parent_folder_id', true);
  var parent = parentId ? getFolder_(parentId) : DriveApp.getRootFolder();
  var result = findOrCreateFolder_(parent, name);
  var out = formatFolder_(result.folder);
  out.already_existed = !result.created;
  return out;
}

function driveMoveFile_(params) {
  var file = getFile_(requireId_(params, 'file_id'));
  var folder = getFolder_(requireId_(params, 'folder_id'));
  file.moveTo(folder);
  return { moved: true, file: formatFile_(file), folder: formatFolder_(folder) };
}

function fileExtension_(name) {
  var dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
}

function driveSaveAttachments_(params) {
  var message = getMessage_(requireId_(params, 'message_id'));
  var folderName = checkDriveName_(requireString_(params, 'folder_name', { max: 200 }));
  var maxBytes = requireInt_(params, 'max_bytes', 1, MAX_ATTACHMENT_BYTES_HARD_LIMIT_);
  var allowed = requireStringList_(params, 'allowed_extensions').map(function (ext) { return ext.toLowerCase(); });

  var target = findOrCreateFolder_(DriveApp.getRootFolder(), folderName);
  var folder = target.folder;
  var saved = [];
  var skipped = [];
  message.getAttachments().forEach(function (attachment) {
    var name = attachment.getName();
    var size = attachment.getSize();
    if (size > maxBytes) {
      skipped.push({ name: name, reason: 'Too large (' + (size / 1048576).toFixed(1) + ' MB; limit ' + (maxBytes / 1048576) + ' MB).' });
    } else if (allowed.indexOf(fileExtension_(name)) === -1) {
      skipped.push({ name: name, reason: "File type '." + (fileExtension_(name) || '?') + "' is not supported." });
    } else if (folder.getFilesByName(name).hasNext()) {
      skipped.push({ name: name, reason: 'A file with this name is already in the folder.' });
    } else {
      var file = folder.createFile(attachment.copyBlob());
      saved.push({ name: file.getName(), size: size, file_id: file.getId(), url: file.getUrl() });
    }
  });
  var out = { folder: formatFolder_(folder), folder_created: target.created, saved: saved, skipped: skipped };
  return out;
}
