"""Store a PAT using Omnigent's existing OS-keychain secret service."""

import argparse
import getpass

from omnigent.onboarding.secrets import store_secret

parser = argparse.ArgumentParser()
parser.add_argument("name", help="Name used in keychain:<name>; never the token itself")
args = parser.parse_args()
value = getpass.getpass("Airbrx PAT (hidden): ")
if not value:
    raise SystemExit("No PAT entered")
store_secret(args.name, value)
print("Stored credential reference: keychain:" + args.name)
