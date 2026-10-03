# Connect a Google Form to local n8n (read-only)

This runbook is for the local n8n reference workflow in this repository. It uses a Google Cloud OAuth app owned by the account holder. Keep the workflow inactive until the credential and Form ID are verified.

## What the connection can read

The workflow needs these two read-only scopes:

- `https://www.googleapis.com/auth/forms.body.readonly` — Form question schema.
- `https://www.googleapis.com/auth/forms.responses.readonly` — submitted responses.

The grant allows this local n8n instance to read Forms data available to the Google account that approves it. Do not use a client/customer account or real prospect responses for this practice run. The OAuth client secret and granted tokens belong in n8n/Google's credential stores; never paste them into chat, workflow JSON, `.env`, or Git.

## Owner setup in Google Cloud

1. Open Google Cloud Console and create or select a project for this local n8n test.
2. Enable the **Google Forms API** in that project.
3. Configure Google Auth Platform/OAuth consent for **External** and keep the app in **Testing**. Add only the Google account you will use as a test user. Do not publish the OAuth app.
4. In n8n, start creating a **Generic OAuth2 API** credential for the HTTP Request nodes. Copy the exact **OAuth Redirect URL** shown by n8n.
5. In Google Cloud, create an OAuth client of type **Web application** and add that exact URL under **Authorized redirect URIs**. The local n8n URL should be based on `http://localhost:5678/rest/oauth2-credential/callback`; use the value displayed by n8n exactly.
6. Enter the resulting Client ID and Client Secret directly into the n8n credential form. Do not send either value to the agent.
7. Configure the credential as Authorization Code with:
   - Authorization URL: `https://accounts.google.com/o/oauth2/v2/auth`
   - Access Token URL: `https://oauth2.googleapis.com/token`
   - Scopes: the two read-only scope URLs listed above, separated by a space
   - Authentication: **Body**
   - Auth URI query parameter: `access_type=offline`
8. Use n8n's **Connect my account** action and review the Google consent screen. Confirm the account shown is the intended test account and the consent requests only the two Forms read-only scopes. Save the credential in n8n.

Google documents the OAuth client, test-user, redirect-URI, and consent flow in [n8n's self-hosted Google OAuth guide](https://docs.n8n.io/integrations/builtin/credentials/google/oauth-single-service/) and [Google's OAuth web-server guide](https://developers.google.com/identity/protocols/oauth2/web-server). The local callback is suitable for development; it does not make the workflow publicly reachable.

## Owner setup in n8n

After the credential is connected:

1. Import `workflows/n8n/lead-management-google-forms-api.local.json` into the local n8n instance.
2. Select the new OAuth credential on both Google HTTP Request nodes.
3. Replace `REPLACE_WITH_PRIVATE_GOOGLE_FORM_ID` with the ID from the Form URL. Keep that ID in the local n8n workflow, not in committed exports or issue text.
4. Keep the workflow inactive and run one manual test with a synthetic-only Form. The export currently uses `DRY_RUN`; it does not consume/advance the source cursor in that mode.
5. Check the execution result and local API response before deciding whether to submit a synthetic test response. Publishing a Form can make it available to respondents; do that only when the owner intentionally chooses its audience and access settings.

The repository contains a separate manual preview that uses synthetic Form data and makes no Google request. The polling workflow has not yet been imported or executed in n8n, and no Google OAuth grant has been made.
