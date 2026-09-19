# Concise reports and interactive screens

The skill remains a read-only adapter. IGW owns energy calculations, source selection, alarm coverage and freshness decisions. Alexa presents the report belonging to the authorized home; voice and touch requests follow the same identity and gateway checks.

## Voice commands

Opening **Home Energy** or asking for **energy status** prefers the optional `reports.status.brief_text` provided by IGW. Older gateways that provide only `text` continue to work. The concise report keeps central warnings and active alarms: a healthy report can be short, but safety notices are not truncated to meet a speaking-time target. The personal **Alexa, energy** routine still opens this same default report.

If the optional brief is empty, malformed or longer than 1200 characters, the adapter discards that field and reads the validated full status text, including its warnings. It does not coerce invalid values into speech or reject an otherwise valid snapshot. Invalid required report text still fails closed.

- **Details** reads the full central text for the current report. Without an active report context, it reads the full status report.
- **Repeat** and **refresh** fetch the current report again. Repeat does not replay a cached reading that may have become stale or belong to an expired account link.
- **Battery**, **solar power**, **solar energy today** and **alarms** select the existing individual reports.
- **Power flow**, **how much power is the house using**, **are we importing or exporting power**, and **is the battery charging** all request the central power flow report when configured.

Within an active session, only an allowlisted report name and a boolean detail preference are stored in session attributes. No telemetry, speech, credentials, identity or gateway address is stored there. A new session without this context defaults to status. There is no conversation history across households or skill sessions.

Quick reports do not reopen the microphone. A voice-only device ends the session after the answer. An APL device can keep its screen and touch controls briefly without a listening reprompt. Follow-up speech still depends on Alexa keeping the skill session active; after it closes, invoke Home Energy again. Help is the only response that deliberately invites a further question.

## Screen content and controls

On a device advertising `Alexa.Presentation.APL`, a self-contained APL 1.0 document shows the central report, freshness labels and optional large numeric values for battery charge, solar power and daily solar yield. It does not require hosted images, fonts or APL packages. A screen without this capability receives ordinary speech and an Alexa app card.

The report provides four touch actions:

- **Refresh** requests a new snapshot of the current report and preserves its detail mode.
- **Battery** switches to battery charge.
- **Today** switches to daily solar yield.
- **Details** requests the full central text for the current report.

Buttons issue an APL `UserEvent`. The adapter accepts only its known document token, fixed action argument and matching component source. It then repeats the normal skill, timestamp, account-linking and household authorization checks before fetching IGW. Document tokens and session attributes never select an arbitrary URL or bypass authorization. Error and account-linking screens replace old readings and have no report buttons.

Source age is explicitly the age of the MQTT receipt known to IGW **at the snapshot**, not the original physical measurement time. `generated_at` is only the JSON envelope time; it must never be used to label measurements as newly taken. The source-age label does not tick while the screen is displayed. Refresh obtains a new age and report.

Optional metrics are displayed only after strict validation of their unit, status, finite numeric value and unsigned receipt age. Fresh numeric readings also require MQTT connectivity; battery charge must be within 0–100%. Stale, unavailable and unconfigured metrics never display a value. Missing or malformed optional metrics retain the central text-only card, preserving the original minimum API contract. Source paths are never copied to the screen.

The serialized response remains within the 16 KiB Worker relay limit. For unusually large escaped reports, the adapter first removes a duplicate app card, then substitutes a display explanation, and finally falls back to speech with a small card if needed. It never truncates the spoken warning or changes freshness labels to make a screen fit.

## Optional power flow

IGW must explicitly enable the optional flow sources. This adds `reports.flow` and the optional `load_power`, `grid_power` and `battery_power` metrics, each using the existing metric shape. Old gateways or homes without flow configuration receive a helpful setup response; their existing reports continue to work.

Flow readings use watts. The screen labels signed readings explicitly:

- Configured AC consumption reflects only the load sources selected in IGW, which may not include every AC or DC load.
- Positive grid power means import; negative grid power means export.
- Positive battery power means charging; negative battery power means discharging.

The adapter does not infer missing values, invent a power-balance equation or draw directional arrows from invalid measurements. The full flow report supplies the authoritative description of partial configuration, source availability and net direction. Individual valid metrics can remain visible when another metric is unconfigured, with a separate freshness label for each.

## Transient failures and response time

A gateway read can retry once after HTTP 502, 503 or 504, or a classified timeout or connection failure. Both attempts share the original gateway timeout; the second receives only the remaining budget and is skipped when less than 100 milliseconds remain. There is no retry delay. The webhook's existing overall deadline still includes signature verification, identity, storage and gateway work.

For personal deployments, a caller deadline bounds response latency while an eight-slot semaphore bounds outstanding transport threads. Python cannot cancel an underlying system DNS lookup or a slow urllib read: after the caller times out, that thread may continue and keeps its slot until the transport actually finishes. Exhausted slots fail promptly as unavailable rather than creating more threads. Late HTTP error responses are closed, and late readings are never returned as current reports. Public household requests retain their pinned HTTPS transport and bounded resolver admission.

Authentication failures, redirects, rate limits, malformed JSON, invalid report text, expired envelopes, certificate failures and network-policy rejections are not retried. Every attempt preserves the same HTTPS, credential, no-redirect and per-household network policy. There is no retry of a write operation: this adapter performs only the read-only energy GET.

## Deployment and acceptance

Deploy the adapter backend and rebuild the updated `skill-package/interactionModels/custom/en-US.json` in the Alexa Developer Console to enable the new spoken intents. APL support must remain enabled in the skill interfaces. Existing invocation names, account linking and credentials do not change. A backend-only rollout enables concise speech and touch handling; new voice samples also require the interaction-model build.

Automated tests use synthetic data to check older gateway compatibility, brief and detailed speech, signed flow values, malformed optional metrics, receipt-age labels, byte limits, retry budgets, touch routing and authorization on every new request type. They do not prove physical layout, touch delivery, Alexa voice recognition or Samsung compatibility. Acceptance on a real device should verify the four buttons, a concise launch, full details, an expired or unlinked account, and a report becoming unavailable after a previously successful screen. Record only sanitized results.

References: [APL UserEvent requests](https://developer.amazon.com/en-US/docs/alexa/alexa-presentation-language/apl-interface.html#userevent-request), [APL 1.0 SendEvent](https://developer.amazon.com/en-US/docs/alexa/alexa-presentation-language/apl-standard-commands-v1.html#sendevent-command).
