import os
import json
import httpx

_user_token: str | None = None


def set_user_token(token: str | None) -> None:
    """Store the verified IAS user token for the current request."""
    global _user_token
    _user_token = token


def get_user_token() -> str | None:
    return _user_token


def _get_dest_service_creds() -> dict:
    """Read BTP Destination service credentials from VCAP_SERVICES (CF) or flat env vars (local)."""
    vcap = json.loads(os.environ.get("VCAP_SERVICES", "{}"))
    dest_bindings = vcap.get("destination", [])
    if dest_bindings:
        return dest_bindings[0]["credentials"]
    return {
        "url":          os.environ["DEST_AUTH_URL"],
        "uri":          os.environ["DEST_URI"],
        "clientid":     os.environ["DEST_CLIENT_ID"],
        "clientsecret": os.environ["DEST_CLIENT_SECRET"],
    }


def get_sfsf_token_and_url() -> tuple[str, str]:
    """Fetch SFSF bearer token and base URL via BTP Destination Service.

    Principal propagation flow:
      1. client_credentials → DS service token
      2. IAS user token → jwt-bearer against subaccount XSUAA → XSUAA user token
      3. GET /destinations/sfsf-conn with X-user-token: <xsuaa_user_token>
         → Destination Service generates SAML assertion for that user
      4. Returns SFSF bearer token for the propagated user

    Falls back to no X-user-token if no IAS token is present (needs SystemUser on destination).

    Returns:
        (access_token, sfsf_base_url)
    """
    creds = _get_dest_service_creds()
    user_token = get_user_token()

    # Step 1: client_credentials for Destination Service
    ds_resp = httpx.post(
        creds["url"] + "/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(creds["clientid"], creds["clientsecret"]),
        timeout=15,
    )
    ds_resp.raise_for_status()
    ds_token = ds_resp.json()["access_token"]

    # Step 2: Pass user token directly as X-user-token header
    # The Destination Service supports IAS tokens directly (token-type: ["xsuaa", "ias"])
    # so we can pass the IAS JWT as X-user-token without exchanging it first.
    headers = {"Authorization": f"Bearer {ds_token}"}
    if user_token:
        headers["X-user-token"] = user_token
        print("[SFSF] IAS token set as X-user-token for principal propagation", flush=True)
    else:
        print("[SFSF] No user token — no principal propagation", flush=True)

    # Step 3: fetch destination with user token for SAML assertion generation
    dest_resp = httpx.get(
        f"{creds['uri']}/destination-configuration/v1/destinations/sfsf-conn",
        headers=headers,
        timeout=15,
    )
    dest_resp.raise_for_status()
    dest = dest_resp.json()

    auth_tokens = dest.get("authTokens", [])
    if not auth_tokens:
        raise RuntimeError(
            "No authTokens returned for sfsf-conn destination. "
            "Verify the destination is configured with OAuth2SAMLBearerAssertion."
        )

    token_entry = auth_tokens[0]
    if token_entry.get("error"):
        raise RuntimeError(
            f"sfsf-conn token exchange failed: {token_entry['error']} — {token_entry.get('message', '')}"
        )
    access_token = token_entry["value"]
    if not access_token:
        raise RuntimeError(
            f"sfsf-conn returned an empty token. Full authTokens entry: {token_entry}"
        )

    sfsf_url = dest["destinationConfiguration"]["URL"].rstrip("/")
    return access_token, sfsf_url


def sfsf_get(path: str, params: dict | None = None) -> dict:
    """Execute a GET request against the SFSF OData API.

    Args:
        path:   OData path relative to the SFSF base URL, e.g. '/odata/v2/User'
        params: Optional query parameters dict
    
    Returns:
        JSON response dict, or an {"error": "message"} dict on failure.
    """
    try:
        token, base_url = get_sfsf_token_and_url()
    except Exception as e:
        error_msg = str(e)
        if "LGN0022" in error_msg or "External user" in error_msg:
            return {"error": "You are not authorized to access SuccessFactors. External users are not supported."}
        return {"error": f"Failed to authenticate with SuccessFactors: {error_msg[:200]}"}
    
    default_params = {"$format": "json"}
    if params:
        default_params.update(params)

    resp = httpx.get(
        f"{base_url}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        params=default_params,
        timeout=30,
    )
    
    # Handle HTTP errors gracefully
    if resp.status_code == 403:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message", {}).get("value", "")
        except Exception:
            msg = resp.text[:200]
        if "LGN0022" in msg or "External user" in msg:
            return {"error": "You are not authorized to access this information. External users are not supported in SuccessFactors."}
        return {"error": f"You are not authorized to access this information. Please contact your administrator. Details: {msg[:100]}"}
    
    elif resp.status_code == 401:
        return {"error": "Authentication failed. Your session may have expired. Please try again or contact your administrator."}
    
    elif resp.status_code >= 400:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message", {}).get("value", resp.text[:200])
        except Exception:
            msg = resp.text[:200]
        return {"error": f"SuccessFactors returned an error: {msg[:200]}"}
    
    return resp.json()
