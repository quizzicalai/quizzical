"""OAuth 1.0a signer verified against the canonical example from the X
developer docs ("Creating a signature")."""
from social_agent.oauth1 import authorization_header, pct, sign, signature_base_string

# Canonical values from https://developer.x.com/.../creating-a-signature
CONSUMER_KEY = "xvz1evFS4wEEPTGEFPHBog"
CONSUMER_SECRET = "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw"
TOKEN = "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"
TOKEN_SECRET = "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"
NONCE = "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"
TIMESTAMP = "1318622958"

URL = "https://api.twitter.com/1.1/statuses/update.json"
FORM = {"status": "Hello Ladies + Gentlemen, a signed OAuth request!"}
QUERY = {"include_entities": "true"}

EXPECTED_SIGNATURE = "hCtSmYh+iHYCEqBWrE7C7hYmtUk="


def _oauth_params():
    return {
        "oauth_consumer_key": CONSUMER_KEY,
        "oauth_nonce": NONCE,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": TIMESTAMP,
        "oauth_token": TOKEN,
        "oauth_version": "1.0",
    }


def test_percent_encoding_is_rfc3986():
    assert pct("Hello Ladies + Gentlemen") == "Hello%20Ladies%20%2B%20Gentlemen"
    assert pct("~-._") == "~-._"


def test_signature_matches_x_docs_example():
    params = {**QUERY, **FORM, **_oauth_params()}
    base = signature_base_string("POST", URL, params)
    assert sign(base, CONSUMER_SECRET, TOKEN_SECRET) == EXPECTED_SIGNATURE


def test_authorization_header_carries_expected_signature():
    header = authorization_header(
        "POST", URL,
        consumer_key=CONSUMER_KEY, consumer_secret=CONSUMER_SECRET,
        token=TOKEN, token_secret=TOKEN_SECRET,
        query_params=QUERY, form_params=FORM,
        nonce=NONCE, timestamp=TIMESTAMP,
    )
    assert header.startswith("OAuth ")
    assert f'oauth_signature="{pct(EXPECTED_SIGNATURE)}"' in header
    assert 'oauth_consumer_key="xvz1evFS4wEEPTGEFPHBog"' in header
