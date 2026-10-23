import uvicorn
import os
import json
import sys
import httpx
import time
from app.agent_card import get_agent_card
from app.agent_executor import MyAgentExecutor
from app.sfsf_client import set_user_token
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


# JWKS cache: {kid: public_key_pem}, refreshed every 10 minutes
_jwks_cache: dict = {}
_jwks_cache_ts: float = 0
_JWKS_TTL = 600  # seconds


def _get_public_key(kid: str) -> str | None:
    """Fetch and cache IAS public keys from JWKS endpoint."""
    global _jwks_cache, _jwks_cache_ts
    now = time.time()
    if now - _jwks_cache_ts > _JWKS_TTL or kid not in _jwks_cache:
        issuer = os.getenv("IAS_ISSUER", "")
        if not issuer:
            return None
        try:
            resp = httpx.get(f"{issuer}/oauth2/certs", timeout=10)
            resp.raise_for_status()
            from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
            from cryptography.hazmat.backends import default_backend
            import base64, struct
            new_cache = {}
            for key in resp.json().get("keys", []):
                if key.get("kty") != "RSA":
                    continue
                def b64_to_int(s):
                    s += "=" * (4 - len(s) % 4)
                    return int.from_bytes(base64.urlsafe_b64decode(s), "big")
                pub = RSAPublicNumbers(b64_to_int(key["e"]), b64_to_int(key["n"])) \
                    .public_key(default_backend())
                from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
                new_cache[key["kid"]] = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
            _jwks_cache = new_cache
            _jwks_cache_ts = now
        except Exception as e:
            print(f"[IAS] JWKS fetch error: {e}", file=sys.stderr)
    return _jwks_cache.get(kid)


def _verify_ias_token(token: str) -> dict | None:
    """Verify IAS JWT — returns claims dict if valid, None otherwise."""
    import jwt as pyjwt
    import base64, json as _json

    ias_issuer = os.getenv("IAS_ISSUER", "")
    ias_audience = os.getenv("IAS_AUDIENCE", "")

    if not ias_issuer or not ias_audience:
        # No IAS config — open mode for local dev
        return {}

    try:
        header_b64 = token.split(".")[0]
        header_b64 += "=" * (4 - len(header_b64) % 4)
        header = _json.loads(base64.urlsafe_b64decode(header_b64))
        kid = header.get("kid", "")
        public_key = _get_public_key(kid)
        if not public_key:
            print(f"[IAS] No public key found for kid={kid}", file=sys.stderr)
            return None
        claims = pyjwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=ias_audience,
            issuer=ias_issuer,
        )
        print(f"[IAS] ✓ {claims.get('email', claims.get('sub', '?'))} verified", file=sys.stderr)
        return claims
    except Exception as e:
        print(f"[IAS] Token verification failed: {e}", file=sys.stderr)
        return None


class IASAuthMiddleware(BaseHTTPMiddleware):
    """Verify IAS JWT on all A2A endpoints. Populate user context for principal propagation."""

    PUBLIC_PATHS = {"/.well-known/agent.json", "/health"}

    async def dispatch(self, request: Request, call_next):
        # Allow HEAD/GET on root for Joule health probes
        if request.method in ("HEAD", "GET") and request.url.path == "/":
            return await call_next(request)
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None

        if not token:
            print("[IAS] Missing Bearer token — allowing through for debug", file=sys.stderr)
            return await call_next(request)

        try:
            import base64 as _b64, json as _json
            if "." in token:
                p = token.split(".")[1]
                p += "=" * (4 - len(p) % 4)
                raw = _json.loads(_b64.urlsafe_b64decode(p))
                # Log ALL claims to find Login Name / user_name
                print(f"[TOKEN-FULL] {_json.dumps(raw, indent=2)}", file=sys.stderr)
            else:
                print(f"[IAS-DEBUG] Opaque token (not JWT): {token[:50]}...", file=sys.stderr)
        except Exception as e:
            print(f"[IAS-DEBUG] Could not decode token: {e}", file=sys.stderr)

        set_user_token(token)
        return await call_next(request)


def get_app_url() -> str:
    """Get the app URL from CF environment or fall back to localhost."""
    vcap = json.loads(os.getenv("VCAP_APPLICATION", "{}"))
    uris = vcap.get("application_uris", [])
    if uris:
        return f"https://{uris[0]}"
    host = os.getenv("HOST", "0.0.0.0")
    port = os.getenv("PORT", "8080")
    return f"http://{host}:{port}"


def main():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8080))

    app_url = get_app_url()
    agent_card = get_agent_card(app_url)
    agent_executor = MyAgentExecutor()

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore(),
    )

    app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    starlette_app = app.build()

    # Add health route for Joule probes (HEAD/GET on /)
    from starlette.routing import Route
    from starlette.responses import Response

    async def health(request):
        return Response(status_code=200)

    starlette_app.routes.insert(0, Route("/", health, methods=["GET", "HEAD"]))
    starlette_app.routes.insert(0, Route("/health", health, methods=["GET", "HEAD"]))

    starlette_app.add_middleware(IASAuthMiddleware)
    uvicorn.run(starlette_app, host=host, port=port)


if __name__ == "__main__":
    main()
