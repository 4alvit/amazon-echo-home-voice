"""Load the ASK model/crypto stack before accepting the first voice request."""

preload_app = True


def on_starting(server):
    from amazon_echo_home_voice.accounts import mode

    if mode() == "multi_household":
        from amazon_echo_home_voice.oauth import OAuthConfig
        from amazon_echo_home_voice.tenant_store import TenantStore

        OAuthConfig.from_env()
        TenantStore.from_env()

    from ask_sdk_core.serialize import DefaultSerializer
    from ask_sdk_model import RequestEnvelope
    from ask_sdk_webservice_support.verifier import RequestVerifier, TimestampVerifier

    RequestVerifier()
    TimestampVerifier(tolerance_in_millis=150000)
    DefaultSerializer().deserialize(
        '{"version":"1.0","request":{"type":"LaunchRequest","timestamp":"2026-01-01T00:00:00Z"}}',
        RequestEnvelope,
    )
