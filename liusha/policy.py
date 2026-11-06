"""Pure decisions for tests/simulations; no API calls, timers or hidden work."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SilentPolicy:
    due_seconds: int = 35 * 60
    expired_seconds: int = 50 * 60
    rebuild_tokens: int = 10000

    def decide(self, *, now, activity, last_refresh=0, same_activity=False,
               disabled=False, verified_session=True, config_changed=False,
               session_busy=False):
        if not verified_session:
            return "invalid_session"
        if activity <= 0 or activity > now:
            return "no_verified_activity"
        if config_changed:
            return "configuration_changed_wait_for_chat"
        if same_activity and disabled:
            return "paused_after_failure"
        anchor = max(activity, last_refresh if same_activity else 0)
        if now - anchor >= self.expired_seconds:
            return "expired_wait_for_chat"
        if now - anchor < self.due_seconds:
            return "not_due"
        return "native_chat_owns_session" if session_busy else "ready"

    def after_probe(self, usage):
        if usage is None:
            return "no_usage_wait_for_chat", True
        if (int(usage.get("cache_read_input_tokens") or 0) <= 0
                or int(usage.get("cache_creation_input_tokens") or 0) >= self.rebuild_tokens):
            return "unexpected_rebuild_wait_for_chat", True
        return "refreshed", False


def visible_idle_decision(*, hour, minute, user_started_day, message_age,
                          usage_age, same_session, busy=False,
                          has_usage=True, night_already_sent=False):
    """Production policy's 01:00–09:00 quiet window and 50–55 min bubble gate.

    Ages are seconds. The caller must obtain user_started_day from *real* user
    activity rather than any automatic message. Check service-active separately.
    """
    if busy:
        return "chat_active"
    if 1 <= hour < 9:
        if hour == 1 and minute < 4 and not night_already_sent:
            if not has_usage or not same_session or usage_age >= 55 * 60:
                return "night_cache_too_old"
            return "night_ready"
        return "outside_window"
    if not user_started_day:
        return "wait_for_user_today"
    if message_age < 50 * 60:
        return "recent_message"
    if not has_usage:
        return "no_usage"
    if usage_age < 50 * 60:
        return "recent_model_call"
    if usage_age >= 55 * 60:
        return "cache_too_old"
    if not same_session:
        return "session_changed"
    return "ready"
