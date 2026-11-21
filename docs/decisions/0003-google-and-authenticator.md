# 0003: Google OIDC and authenticator protection

Status: accepted. Date: 2026-11-17.

## Context and decision

Preserve Django sessions and tenant permissions. Use Authlib Google discovery with authorization-code flow, S256 PKCE, session state and nonce. Validate signed tokens, issuer, audience, expiry and verified email. Persist subject/user/email only, never provider tokens. Existing email matches require explicit linking after local authentication and password confirmation or recent Google authentication.

Use PyOTP with Fernet-encrypted secrets and PostgreSQL row-locked counter consumption. Generate ten random 80-bit recovery codes, display once and store SHA-256 digests. Sessions store a factor-version proof. HTTP and authenticated WebSocket paths enforce MFA; stream data delivery rechecks the active user, server session and current factor. Production defaults require enrollment for owners and platform staff; debug enrollment enforcement is configurable. All primary-login paths, including admin, use the same completion flow.

## Alternatives and consequences

Custom JWT validation adds protocol risk. Automatic email merging risks account takeover. Replacing Django auth would disrupt working paths. TOTP is interoperable but not phishing-resistant. Passkeys, assisted recovery after total factor loss, Google disconnection and session inventory remain work.

Account security preserves ivory/espresso tokens and typography, with section navigation and native details/forms. Rare removal actions remain collapsed. Browser checks verified keyboard access, phone/tablet layout, error feedback and successful MFA login; they do not establish full accessibility acceptance or an exact visual reproduction of the generated reference.
