# Supported English (US) requests

Invocation name: **home energy**. Include it when starting a direct custom-skill request. An optional personal Alexa Routine can provide the shorter overview command **Alexa, energy** without renaming the skill.

- **Alexa, open the home energy skill** — immediately reads IGW `reports.status` and displays the energy overview on supported screens. No follow-up request is needed; the invocation name remains **home energy**.
- **Alexa, ask home energy what is the battery charge** — `BatteryIntent`, IGW `reports.battery`.
- **Alexa, ask home energy what is the solar power** — `SolarIntent`, IGW `reports.solar`.
- **Alexa, ask home energy how much solar energy did we produce today** — `SolarTodayIntent`, IGW `reports.solar_today`.
- **Alexa, ask home energy are there any alarms** — `AlarmStatusIntent`, IGW `reports.alarms`.
- **Alexa, ask home energy what is the energy status** — `StatusIntent`, IGW `reports.status`.
- **Help** — describes these requests and keeps the session open.
- **Stop** or **cancel** — ends the session.

After a help prompt, omit the invocation name for the next request. Opening the skill already returns the status report; use a complete request to start another report after the session ends. Exact training phrases are in `skill-package/interactionModels/custom/en-US.json`. Requests to control devices cannot cause writes.

## Optional personal shortcut

For **Alexa, energy**, create an enabled routine with the **Voice** trigger `energy` and one **Custom** action, `open the home energy skill`. Enter both without the wake word. Choose **The device you speak to** for output when offered. If Custom is unavailable, choose **Skills → Your Skills → Home Energy** and its opening action when listed; development skills are not guaranteed to appear in every app or account. See the [setup and device-verification steps](../README.md#optional-short-command-alexa-energy).

The routine must be configured in each Alexa account that needs the shortcut. The default invocation stays **home energy**, and all existing authentication and gateway requirements still apply. A generic one-word invocation does not meet Amazon's [naming requirements](https://developer.amazon.com/en-US/docs/alexa/interaction-model-design/design-the-invocation-name-for-your-skill.html); Amazon separately supports [opening custom skills from personal routines](https://developer.amazon.com/en-US/blogs/alexa/post/cf65c68e-f3df-475e-939d-4ea2771b20b7/tell-your-customers-they-can-now-invoke-your-skill-from-routine).

Where Alexa+ web chat offers routine creation, the README includes the exact
request. Verify the saved action, output device and enabled state in the Alexa
app, then test the phrase physically. A creation acknowledgment or trigger-only
chat response is not end-to-end verification.
