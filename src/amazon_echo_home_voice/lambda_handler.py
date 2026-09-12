"""AWS Lambda entry point for a private Alexa custom skill."""

from datetime import datetime, timezone
import os
import re

from .gateway import GatewayConfig, GatewayError, UNAVAILABLE_TEXT, fetch_energy


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


def lambda_handler(event, context):
    """Handle only the explicit energy intents and standard session lifecycle."""
    request = validate_event(event)
    kind = request.get("type")
    if kind == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    if kind == "LaunchRequest":
        return _speech("Welcome to Home Energy. " + HELP_TEXT, end_session=False)
    if kind != "IntentRequest":
        raise PermissionError("Unsupported Alexa request type")
    intent = request.get("intent")
    if not isinstance(intent, dict) or not isinstance(intent.get("name"), str):
        raise PermissionError("Invalid Alexa intent")
    name = intent["name"]
    if name in {"AMAZON.StopIntent", "AMAZON.CancelIntent"}:
        return _speech("Goodbye.")
    if name in {"AMAZON.HelpIntent", "AMAZON.FallbackIntent"}:
        return _speech(HELP_TEXT, end_session=False)
    if name not in INTENTS:
        # Never turn arbitrary intent names into gateway paths or write requests.
        return _speech("I can only report home energy data. " + HELP_TEXT, end_session=False)
    try:
        payload = fetch_energy(GatewayConfig.from_env())
        text = payload["reports"][INTENTS[name]]["text"]
    except GatewayError:
        text = UNAVAILABLE_TEXT
    return _speech(text)
