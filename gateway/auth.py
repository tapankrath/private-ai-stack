"""Authentication modes.

single        Tier 1: one owner. Callers (Open WebUI) present the gateway key.
proxy_header  Tier 2: a trusted proxy presents the gateway key AND identifies the user with
              headers (Open WebUI: X-OpenWebUI-User-*; Authentik outpost: X-authentik-*).
              Headers are ignored unless the gateway key is valid, so users cannot spoof them.
oidc          Tier 3: everything in proxy_header, plus direct API clients with an OIDC bearer
              token (JWT) verified against the identity provider's JWKS (issuer, audience, expiry).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from .settings import Settings
from .store import Store, UserCtx


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Identity:
    id: str
    display_name: str
    role: str
    groups: list[str] | None      # None = keep the groups already stored for this user
    via: str


def _bearer(headers) -> str | None:
    h = headers.get("authorization") or ""
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return headers.get("x-gateway-key")


def _key_ok(token: str | None, settings: Settings) -> bool:
    return bool(token and settings.gateway_key and hmac.compare_digest(token.encode(), settings.gateway_key.encode()))


def _role(groups: list[str], declared: str | None, settings: Settings) -> str:
    if (declared or "").lower() == "admin" or set(groups) & set(settings.admin_groups):
        return "admin"
    return "member"


def identify(headers, settings: Settings, jwks_client=None, store: Store | None = None) -> Identity:
    token = _bearer(headers)
    mode = settings.auth_mode
    if token and token.startswith("gwu_") and store is not None:      # personal access token (web UI)
        u = store.resolve_token(token)
        if not u:
            raise AuthError(401, "Token expired or revoked.")
        return Identity(u["id"], u["display_name"] or u["id"], u["role"], u["groups"], "token")
    if not settings.gateway_key and mode != "oidc":
        raise AuthError(500, "Gateway key is not configured (GATEWAY_GATEWAY_KEY_FILE). Refusing to run open.")
    if _key_ok(token, settings):
        if mode == "single":
            return Identity(settings.single_user_id, settings.single_user_id, "admin", [], "key")
        uid = (headers.get("x-openwebui-user-email") or headers.get("x-openwebui-user-id")
               or headers.get("x-authentik-email") or headers.get("x-authentik-username"))
        if not uid:
            raise AuthError(401, "Trusted proxy did not identify the user.")
        name = headers.get("x-openwebui-user-name") or headers.get("x-authentik-name") or uid
        # Groups are trusted only from the identity provider's forward-auth headers. Open WebUI does
        # not assert groups, so for it the groups already stored (synced from the IdP / CLI) apply.
        groups = None
        if headers.get("x-authentik-username") and not headers.get("x-openwebui-user-email"):
            groups = [g for g in (headers.get("x-authentik-groups") or "").split("|") if g]
        return Identity(uid.lower(), name, _role(groups or [], headers.get("x-openwebui-user-role"), settings), groups, "proxy")
    if mode == "oidc" and token and token.count(".") == 2:
        return _verify_jwt(token, settings, jwks_client)
    raise AuthError(401, "Missing or invalid credentials.")


def _verify_jwt(token: str, settings: Settings, jwks_client=None) -> Identity:
    import jwt  # PyJWT

    try:
        client = jwks_client or jwt.PyJWKClient(settings.oidc_jwks_url)
        key = client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256", "ES256", "EdDSA"], audience=settings.oidc_audience,
                            issuer=settings.oidc_issuer, options={"require": ["exp", "iss", "sub"]})
    except Exception as exc:  # any verification failure is a 401
        raise AuthError(401, f"Invalid token: {type(exc).__name__}")
    groups = claims.get(settings.oidc_groups_claim) or []
    if isinstance(groups, str):
        groups = [groups]
    uid = (claims.get("email") or claims.get("preferred_username") or claims["sub"]).lower()
    return Identity(uid, claims.get("name") or uid, _role(groups, None, settings), list(groups), "oidc")


def user_ctx(identity: Identity, store: Store) -> UserCtx:
    existing = store.get_user(identity.id)
    groups = identity.groups if identity.groups is not None else (existing["groups"] if existing else [])
    role = identity.role
    if identity.groups is None and existing and existing["role"] == "admin" and identity.via == "proxy":
        role = "admin"                                   # admin granted via IdP sync / CLI is kept
    if (not existing or existing["role"] != role or existing["groups"] != sorted(groups)
            or existing["display_name"] != identity.display_name):
        store.upsert_user(identity.id, identity.display_name, role, groups)
    return UserCtx(identity.id, role, groups, identity.display_name, store.is_unlocked(identity.id))
