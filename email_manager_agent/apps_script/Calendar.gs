/** Calendar operations on the user's default calendar. Recurring events are not changed. */

var CALENDAR_MAX_RESULTS_ = 250;
var CALENDAR_DESCRIPTION_CHARS_ = 1000;

function calendar_() {
  return CalendarApp.getDefaultCalendar();
}

function formatEvent_(event) {
  return {
    id: event.getId(),
    title: event.getTitle(),
    start: isoUtc_(event.getStartTime()),
    end: isoUtc_(event.getEndTime()),
    all_day: event.isAllDayEvent(),
    location: event.getLocation(),
    description: String(event.getDescription() || '').slice(0, CALENDAR_DESCRIPTION_CHARS_),
    attendees: event.getGuestList().map(function (guest) { return guest.getEmail(); }),
    my_status: String(event.getMyStatus()),
    recurring: event.isRecurringEvent()
  };
}

function getEvent_(eventId) {
  var event = calendar_().getEventById(eventId);
  if (!event) throw bridgeError_('not_found', 'Calendar event not found: ' + eventId);
  return event;
}

function requireNotRecurring_(event) {
  if (event.isRecurringEvent()) {
    throw bridgeError_('unsupported', 'Recurring events cannot be changed through the agent.');
  }
}

function calendarListEvents_(params) {
  var start = requireDate_(params, 'start');
  var end = requireDate_(params, 'end');
  if (end <= start) throw bridgeError_('invalid_params', 'end must be after start.');
  var options = { max: requireInt_(params, 'max_results', 1, CALENDAR_MAX_RESULTS_, 50) };
  var query = requireString_(params, 'query', { max: 200, optional: true });
  if (query) options.search = query;
  var calendar = calendar_();
  return {
    timezone: calendar.getTimeZone(),
    events: calendar.getEvents(start, end, options).map(formatEvent_)
  };
}

function calendarGetEvent_(params) {
  return formatEvent_(getEvent_(requireId_(params, 'event_id')));
}

function calendarCreateEvent_(params) {
  var title = requireString_(params, 'title', { max: 250 });
  var start = requireDate_(params, 'start');
  var end = requireDate_(params, 'end');
  if (end <= start) throw bridgeError_('invalid_params', 'end must be after start.');
  var attendees = requireEmailList_(params, 'attendees', true);
  var options = {
    description: requireString_(params, 'description', { max: 5000, optional: true }),
    location: requireString_(params, 'location', { max: 500, optional: true }),
    sendInvites: requireBool_(params, 'send_invites', true)
  };
  if (attendees.length) options.guests = attendees.join(',');
  var event = calendar_().createEvent(title, start, end, options);
  return formatEvent_(event);
}

function calendarUpdateEvent_(params) {
  var event = getEvent_(requireId_(params, 'event_id'));
  requireNotRecurring_(event);
  var start = requireDate_(params, 'start', true);
  var end = requireDate_(params, 'end', true);
  if ((start && !end) || (!start && end)) {
    throw bridgeError_('invalid_params', 'start and end must be given together.');
  }
  if (start && end <= start) throw bridgeError_('invalid_params', 'end must be after start.');
  var title = requireString_(params, 'title', { max: 250, optional: true, defaultValue: null });
  var description = params.description === undefined ? null : requireString_(params, 'description', { max: 5000, optional: true });
  var location = params.location === undefined ? null : requireString_(params, 'location', { max: 500, optional: true });
  var addAttendees = requireEmailList_(params, 'add_attendees', true);

  if (start) event.setTime(start, end);
  if (title) event.setTitle(title);
  if (description !== null) event.setDescription(description);
  if (location !== null) event.setLocation(location);
  addAttendees.forEach(function (email) { event.addGuest(email); });
  return formatEvent_(event);
}

function calendarCancelEvent_(params) {
  var event = getEvent_(requireId_(params, 'event_id'));
  requireNotRecurring_(event);
  var summary = formatEvent_(event);
  event.deleteEvent();
  return { cancelled: true, event: summary };
}
