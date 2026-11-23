# Identity, recovery and SSO

## Local accounts and MFA

Administrators provision local accounts; workspace owners separately grant membership. Passwords are 14-128 characters and stored with Argon2id. There is no public registration, email invitation flow or automatic account creation. Email addresses are administrative identifiers; this release does not implement email ownership verification for newly provisioned local accounts. Deliver initial credentials through an approved separate channel.

Account security supports TOTP authenticators with a 30-second step, six digits and a one-step clock window. A previously accepted counter cannot be reused. Enrollment requires the current password, has a ten-minute confirmation window and does not activate until a correct code is entered. The setup secret is AES-GCM encrypted with the application key. Ten independent 128-bit recovery codes are shown once, stored as hashes, and consumed atomically. Rotating them invalidates all old codes. Codes, setup keys and password values are not logged in audit events. Closing a dialog or signing out removes secret-bearing modal content from the DOM.

Set `DATAFORGE_REQUIRE_MFA=true` for a deployment-wide mandatory factor. A successful first password login can access only enrollment/account endpoints until MFA is enabled; it cannot read or alter workspaces. Production preflight requires this setting. Supervised factor reset is an operator CLI action, not an email-link bypass:

```bash
python -m app.cli reset-mfa --email person@your-company.example --reason "Identity independently verified under ticket SEC-123"
```

This removes factors/recovery codes and invalidates sessions, reset links and pending SSO factor challenges. Verify the requester's identity out of band before doing it. The next permitted login must enroll again when MFA is required. Password resets do not disable an existing factor. A person with shell/DB/application-key access remains a trusted privileged operator; this is not protection from a malicious server administrator.

## Password recovery and sessions

Configure a real SMTP host, From address and credentials in the private environment file; use `starttls` or implicit `tls`, with certificate verification. Recovery requests return the same generic message for active and nonexistent accounts. Rate limits apply to accounts and observed IPs. This reduces obvious enumeration; response timing is not proven indistinguishable.

Reset links expire after 15 minutes. Only a token hash is stored; the queued email is encrypted. The URL token is placed in a fragment, removed by the UI before submitting completion, and not put in an HTTP path or referrer. The new password is validated and hashed, all sessions and reset tokens are revoked, and MFA is preserved. Password changes and administrative resets also invalidate outstanding reset links and pending SSO factor challenges. Recovery is explicitly unavailable when SMTP is not configured.

Account security lists active sessions and allows self-revocation. Sessions have eight-hour absolute and thirty-minute idle defaults, with a maximum of five sessions per account. Cookies are HttpOnly/SameSite=Strict and Secure for HTTPS origins. State-changing authenticated requests require CSRF and approved-origin checks. API/worker account and membership checks are not replaced by client-side UI hiding.

## OIDC configuration

Supported provider contract: HTTPS discovery; authorization-code flow; PKCE S256; RS256-signed ID tokens using RSA keys >=2048 bits; explicit issuer/audience/subject, expiration, issued-at and nonce checks. Multi-audience tokens require the correct authorized party. Optional `at_hash` and configured assurance (`acr`) are checked. State and browser binding are single-use/short-lived. Enrolled local MFA must still complete after SSO.

```text
DATAFORGE_OIDC_ISSUER=https://login.your-company.example/exact-issuer
DATAFORGE_OIDC_CLIENT_ID=your-client-id
DATAFORGE_OIDC_CLIENT_SECRET=your-private-secret
DATAFORGE_OIDC_ALLOWED_HOSTS=other-explicit-provider-host.example
DATAFORGE_OIDC_REQUIRED_ACR=
```

The issuer string is exact, including a trailing slash when your provider requires it. Discovery appends `/.well-known/openid-configuration` after removing only the joining slash; issuer comparison is not normalized. Provider authorization/token/JWKS endpoints must use HTTPS and explicitly allowed hostnames. Redirects from metadata/token/key fetches are not followed. Token endpoint client authentication uses HTTP Basic when a secret is supplied, or a public PKCE client when it is absent. Providers requiring different algorithms, private_key_jwt, client_secret_post or nonstandard metadata need an explicitly reviewed adapter; there is no implicit fallback.

Register the exact redirect URI:

```text
https://your-dataforge-host/api/auth/oidc/callback
```

Provision the user first, then link an administrator-verified subject:

```bash
python -m app.cli oidc-link --email person@your-company.example --issuer https://login.your-company.example/exact-issuer --subject verified-provider-subject
python -m app.cli oidc-unlink --issuer https://login.your-company.example/exact-issuer --subject verified-provider-subject
```

Email claims never create or link accounts. Unlinking revokes sessions. Local password sign-in remains available; this is not an SSO-only enforcement feature. Provider logout is not invoked by local sign-out. Test your provider's actual code flow, certificate trust, clock behavior, subject lifecycle and deprovisioning before enabling it for staff.

The supplied tests validate real RSA signatures/claims against a deterministic mock provider transport. No production identity-provider tenant was available here. This is not a claim that your provider has already been integrated.
