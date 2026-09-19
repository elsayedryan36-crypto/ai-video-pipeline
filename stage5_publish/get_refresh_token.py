"""
Run this ONCE, locally, on your own machine (not in the cloud) to get a refresh
token that GitHub Actions can then use forever without you re-logging in.

One-time setup before running this:
  1. https://console.cloud.google.com/ -> create a project
  2. Enable the "YouTube Data API v3"
  3. OAuth consent screen -> External -> add your own Google account as a Test User
  4. Credentials -> Create Credentials -> OAuth client ID -> Desktop app
  5. Download the JSON, save it here as client_secret.json

Then:
    pip install google-auth-oauthlib
    python get_refresh_token.py

It opens a browser, you log in and approve, and it prints a refresh token.
Save that as the GitHub secret YT_REFRESH_TOKEN (and the client id/secret from
the JSON as YT_CLIENT_ID / YT_CLIENT_SECRET).

NOTE: while your OAuth app is in "Testing" mode (the default, and the free
option), refresh tokens expire after 7 days of the app being untouched, and
only the Test Users you added can authorize it. To stop needing to redo this
weekly, submit the app for Google's verification (free, takes review time) --
or just budget 2 minutes every ~week to re-run this script.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)
    print("\n--- Save these as GitHub Actions secrets ---")
    print(f"YT_CLIENT_ID={creds.client_id}")
    print(f"YT_CLIENT_SECRET={creds.client_secret}")
    print(f"YT_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
