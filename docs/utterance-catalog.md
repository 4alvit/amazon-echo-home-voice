# Supported English (US) requests

Invocation name: **home energy**. This is an Alexa custom skill, so include its invocation name when starting a request.

- **Alexa, open home energy** — welcome and help; starts a conversation.
- **Alexa, ask home energy what is the battery charge** — `BatteryIntent`, IGW `reports.battery`.
- **Alexa, ask home energy what is the solar power** — `SolarIntent`, IGW `reports.solar`.
- **Alexa, ask home energy how much solar energy did we produce today** — `SolarTodayIntent`, IGW `reports.solar_today`.
- **Alexa, ask home energy are there any alarms** — `AlarmStatusIntent`, IGW `reports.alarms`.
- **Alexa, ask home energy what is the energy status** — `StatusIntent`, IGW `reports.status`.
- **Help** — describes these requests and keeps the session open.
- **Stop** or **cancel** — ends the session.

After the welcome prompt, omit the invocation name. Exact training phrases are in `skill-package/interactionModels/custom/en-US.json`. Requests to control devices cannot cause writes.
