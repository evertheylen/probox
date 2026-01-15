import json
import sys
import os
import requests
import sys
from datetime import timedelta

SECRETS_FILE = "digitalocean.secrets.json"

def load_secrets():
    if not os.path.exists(SECRETS_FILE):
        return {}
    with open(SECRETS_FILE, "r") as f:
        return json.load(f)

def save_secrets(data):
    # Ensure file permissions are restrictive (Owner read/write only)
    with open(SECRETS_FILE, "w") as f:
        json.dump(data, f, indent=4)
    os.chmod(SECRETS_FILE, 0o600)

def setup():
    redirect_uri = "https://oauth.example.com"
    print("--- DigitalOcean OAuth Setup ---")
    print("1. Configure a new OAuth app on https://cloud.digitalocean.com/account/api/applications'")
    print(f"Assume redirect_uri is {redirect_uri}")
    client_id = input("Enter Client ID: ").strip()
    client_secret = input("Enter Client Secret: ").strip()

    auth_url = (
        f"https://cloud.digitalocean.com/v1/oauth/authorize"
        f"?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code"
        f"&scope=read%20write"
    )

    print(f"\n2. Now click the URL:\n{auth_url}\n")
    auth_code = input("3. Enter the 'code' parameter from the URL you were sent to: ").strip()

    print("Exchanging code for tokens...")
    resp = requests.post("https://cloud.digitalocean.com/v1/oauth/token", data={
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": auth_code,
        "redirect_uri": redirect_uri,
        "scope": "read write",
    })

    data = resp.json()
    if "refresh_token" not in data:
        print(f"FAILED: {data}")
        sys.exit(1)

    save_secrets({
        "CLIENT_ID": client_id,
        "CLIENT_SECRET": client_secret,
        "REFRESH_TOKEN": data["refresh_token"]
    })
    print(f"Setup complete! Secrets saved to {SECRETS_FILE}")

def refresh():
    secrets = load_secrets()
    if not secrets:
        print(f"Error: {SECRETS_FILE} not found. Run 'python {sys.argv[0]} setup' first.")
        sys.exit(1)

    resp = requests.post("https://cloud.digitalocean.com/v1/oauth/token", data={
        "grant_type": "refresh_token",
        "client_id": secrets["CLIENT_ID"],
        "client_secret": secrets["CLIENT_SECRET"],
        "refresh_token": secrets["REFRESH_TOKEN"],

        # none of these work :(
        # TODO manually revoke after some time
        # "duration_seconds": 3600,  # Most common for machine-to-machine
        # "expires_in": 3600,        # Standard response key, sometimes used as request key
        # "ttl": 3600,               # Common in older or custom APIs
        # "requested_lifetime": 3600 # Used in some enterprise OAuth extensions
    })

    data = resp.json()
    if "access_token" not in data:
        print(f"Refresh failed: {data}")
        sys.exit(1)

    expires_in_seconds = data.get("expires_in", 0)

    # Calculate human-readable duration
    duration = str(timedelta(seconds=expires_in_seconds))

    # Rotation: Update refresh token for next time
    secrets["REFRESH_TOKEN"] = data["refresh_token"]
    save_secrets(secrets)

    access_token = data["access_token"]
    print(f"# --- Copy to Container (token expires in {duration}) ---")
    print(f"export DIGITALOCEAN_ACCESS_TOKEN={access_token}")
    print(f"set -x DIGITALOCEAN_ACCESS_TOKEN {access_token}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup()
    else:
        refresh()
