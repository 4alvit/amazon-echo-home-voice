"""Alexa handler for personal and authenticated multi-household deployments."""

from datetime import datetime, timezone
from dataclasses import replace
import os
import re

from .gateway import GatewayConfig, GatewayError, UNAVAILABLE_TEXT, fetch_energy
from .accounts import mode, household_connection, request_timeout, UnlinkedAccount, UnconfiguredHome
from .oauth import OAuthError
from .tenant_store import StoreError
from .visuals import message_visuals, report_visuals
from .diagnostics import gateway_failure, report_statuses


INTENTS = {
    "BatteryIntent": "battery",
    "SolarIntent": "solar",
    "SolarTodayIntent": "solar_today",
    "AlarmStatusIntent": "alarms",
    "StatusIntent": "status",
}
HELP_TEXT = (
    "You can ask about battery charge, solar power, solar energy today, "
    "alarms, or home energy status. What would you like to know?"
)


def _speech(text: str, *, end_session: bool = True) -> dict:
    response = {
        "outputSpeech": {"type": "PlainText", "text": text},
        "shouldEndSession": end_session,
    }
    if not end_session:
        response["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": HELP_TEXT}}
    return {"version": "1.0", "response": response}


def _account_response(event, error):
    if isinstance(error, UnlinkedAccount):
        response = _speech("To hear your home energy reports, open the Alexa app and link your Home Energy account.")
        response["response"]["card"] = {"type": "LinkAccount"}
        return message_visuals(event, response, title="Link your account")
    if isinstance(error, UnconfiguredHome):
        return message_visuals(event, _speech("Your account is linked. Sign in to the Home Energy connection portal and connect your gateway, then ask again."), title="Connect your gateway")
    return _unavailable(event)


def _unavailable(event):
    return message_visuals(event, _speech(UNAVAILABLE_TEXT), title="Energy status", status="Data unavailable")


def validate_event(event: object) -> dict:
    """Fail closed before any network request; Lambda's skill trigger also checks ID."""
    skill_id = os.environ.get("ASK_SKILL_ID", "")
    if not re.fullmatch(r"amzn1\.ask\.skill\.[A-Za-z0-9-]+", skill_id):
        raise PermissionError("Alexa skill ID is not configured")
    if not isinstance(event, dict) or event.get("version") != "1.0":
        raise PermissionError("Invalid Alexa request")
    try:
        context_id = event["context"]["System"]["application"]["applicationId"]
        if context_id != skill_id:
            raise PermissionError("Unrecognized Alexa skill")
        if "session" in event and event["session"]["application"]["applicationId"] != skill_id:
            raise PermissionError("Unrecognized Alexa session")
        request = event["request"]
        if not isinstance(request, dict) or request.get("locale") != "en-US":
            raise PermissionError("Unsupported Alexa request")
        timestamp = request["timestamp"]
        if not isinstance(timestamp, str):
            raise ValueError("Missing timestamp")
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if instant.tzinfo is None or abs((datetime.now(timezone.utc) - instant).total_seconds()) > 150:
            raise PermissionError("Expired Alexa request")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PermissionError("Invalid Alexa request") from exc
    return request


def lambda_handler(event, context, *, deadline=None):
    """Handle only the explicit energy intents and standard session lifecycle."""
    request = validate_event(event)
    kind = request.get("type")
    if kind == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    try:
        current_mode = mode()
        request_timeout(deadline, 1.0)
    except GatewayError as exc:
        gateway_failure(exc)
        return _unavailable(event)
    if kind == "LaunchRequest":
        # Opening the skill is a request for the same overview as StatusIntent.
        name = "StatusIntent"
    elif kind == "IntentRequest":
        intent = request.get("intent")
        if not isinstance(intent, dict) or not isinstance(intent.get("name"), str):
            raise PermissionError("Invalid Alexa intent")
        name = intent["name"]
    else:
        raise PermissionError("Unsupported Alexa request type")
    if name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
        return _speech("Goodbye.")
    if name in {"AMAZON.HelpIntent", "AMAZON.FallbackIntent"}:
        return message_visuals(event, _speech(HELP_TEXT, end_session=False), title="What you can ask")
    if name not in INTENTS:
        # Never turn arbitrary intent names into gateway paths or write requests.
        return message_visuals(event, _speech("I can only report home energy data. " + HELP_TEXT, end_session=False), title="What you can ask")
    try:
        config = household_connection(event, deadline=deadline) if current_mode == "multi_household" else GatewayConfig.from_env()
        if deadline is not None:
            # Recompute after identity and storage work; never reuse the original budget.
            config = replace(config, timeout_seconds=request_timeout(deadline, config.timeout_seconds))
        payload = fetch_energy(config)
        request_timeout(deadline, 1.0, reserve=0)
        text = payload["reports"][INTENTS[name]]["text"]
    except (UnlinkedAccount, UnconfiguredHome, OAuthError, StoreError) as exc:
        return _account_response(event, exc)
    except GatewayError as exc:
        gateway_failure(exc)
        return _unavailable(event)
    report_statuses(payload)
    return report_visuals(event, _speech(text), INTENTS[name], payload)
