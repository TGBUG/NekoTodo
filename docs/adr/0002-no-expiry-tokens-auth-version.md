# Tokens never expire; auth_version is the only revocation gate

Multi-device sync means one Account may hold several valid JWTs at once. Rather than expiry plus refresh tokens, tokens have **no expiry**: once issued, a token stays valid until the Account's `auth_version` is bumped. Bumping happens on password change or via the revoke-all-tokens endpoint, and invalidates every previously issued token at once.

This keeps the token lifecycle trivial: one login = valid forever until explicitly revoked, and the only server-side kill switch is the auth version. Expiry + refresh tokens were rejected (re-login every period on every device, or an extra token kind to manage); a jti denylist was rejected as an extra revocation path we do not yet need.

Consequences: logout is client-side token discard — the token remains server-valid until the auth version changes. A leaked token stays valid until a version bump, so the revoke-all endpoint is the emergency kill switch. Per-token revocation is explicitly out of scope for now.
