# Deployment

The [Dockerfile](../Dockerfile) builds the application image. [compose.yaml](../compose.yaml) publishes PostgreSQL and Redis to Windows loopback. [compose.staging.yaml](../compose.staging.yaml) provides a private staging topology with an ASGI web process, one-shot migrations, four independently supervised workers and private dependency networking. Hosted infrastructure remains a separate acceptance step.

[Topology](#release-topology) · [Release checklist](#release-checklist) · [Backup and restore](#backup-and-restore)

## Release topology

| Service | Configuration |
| --- | --- |
| Web | Build the Python 3.12 image. Its entrypoint runs Uvicorn with ``config.asgi:application``, bind address ``0.0.0.0``, port from ``PORT`` or 8000, and access logs disabled. Route HTTP and WebSocket traffic to this ASGI process. |
| TLS proxy | Set the trusted forwarded HTTPS header at the proxy. Overwrite untrusted client-supplied values. Allow WSS upgrades and configure public hosts and trusted CSRF origins. Keep raw signed URLs and query strings out of access logs. |
| Database | Point the app and workers to a supported PostgreSQL deployment with a session-capable endpoint, restricted credentials and verified TLS. Local verification used PostgreSQL 17. |
| Channels and cache | Use authenticated Redis or compatible Valkey, with verified TLS and permissions for the channel layer. Verify group delivery in the intended network. Local verification used Redis 7. |
| Workers | Supervise the outbox, account mail, platform billing and refund reconciliation independently. See [operations](OPERATIONS.md#workers-and-health). |
| Assets | The build runs ``collectstatic`` and WhiteNoise serves collected files. The app authorizes uploaded media and serves its PostgreSQL bytes. Optional private Supabase archive stays server-side. |
| Email | Configure an approved SMTP relay or the Resend HTTPS backend and verified sender. Check verification, reset and invitation delivery. |

Render for ASGI, Supabase for PostgreSQL or a private media archive, Aiven Valkey, and Resend are candidate services discussed in prior staging notes. Review the actual plan and provision approved services before treating them as part of a release. [Integrations](INTEGRATIONS.md)

### Start an isolated staging stack

Create a protected staging environment file. Set the image reference, the path to that environment file, a dedicated PostgreSQL password, the internal database URL using hostname `postgres` and database `veyra_staging`, and independent Django and encryption keys. Set the allowed hosts and CSRF trusted HTTPS origins for the staging proxy. PostgreSQL and Redis have no published ports. The host proxy reaches the ASGI listener via `127.0.0.1` and the chosen staging port. The app trusts the proxy to overwrite the forwarded HTTPS header.

```powershell
docker compose --project-name veyra-stage --env-file .env.staging --file compose.staging.yaml config --quiet
docker compose --project-name veyra-stage --env-file .env.staging --file compose.staging.yaml up -d --wait
```

The migration container runs first. The web and workers start only after migration succeeds and dependencies are healthy. Worker health checks watch progress, and the process supervisor restarts crashed processes. Have the host monitor report unhealthy processes so a stalled worker receives operator attention. Hosted equivalents should supervise the same process types and report the same readiness distinctions. [Environment variables](ENVIRONMENT.md) · [Operational checks](OPERATIONS.md)

## Release checklist

1. Supply production [configuration](ENVIRONMENT.md) through the web and worker environments. Set debug false, an independent strong secret key, allowed hosts, URLs and required feature keys. Store backed-up encryption keys separately. Give production email a verified sender and transport.
2. Build the image from the approved source. Check that image and app dependencies using the same build configuration intended for release.
3. Review the database and migration plan, confirm an accessible backup, then run the migration once as a release action. Run these commands in the configured image or corresponding release shell.

   ```sh
   python manage.py check --deploy
   python manage.py migrate --plan
   python manage.py migrate --noinput
   ```

4. Start the web and workers with the same application version. Check ``/healthz/`` and ``/readyz/`` before opening traffic. Verify the real proxy with two browser clients and the Redis group probe. Verify both event commit and delivery.
5. Accept external flows separately using approved identities, provider credentials and test recipients. Check WSS reconnect, notification delivery, QRIS settlement, supported refunds, platform checkout, private media archive, and physical printing where used. Record exact environment and evidence. [Payments](PAYMENTS.md) · [Testing](TESTING.md)

To build the image locally from a checkout:

```powershell
docker build -t veyra:local .
docker run --rm --entrypoint python veyra:local --version
```

The 2026-11-25 local release image built with Python 3.12.16. It served liveness, dependency readiness, the landing page and manifest CSS with debug disabled. The local forwarded-HTTPS check returned HSTS. Hosted TLS, email, payments, backup policy, worker supervision and hardware still require separate verification. The non-debug Django check reports HSTS preload guidance until preload and its domain-wide implications are separately accepted.

## Backup and restore

Use PostgreSQL client tools compatible with the server and follow the encrypted backup procedure below. Supply credentials privately through connection service definitions and the approved credential store. The example assumes ``veyra_backup`` is a reviewed source service and ``veyra_recovery`` resolves to an empty, isolated recovery database. Confirm target host and database identity before any restore. Keep the dump and encryption/signing key backups protected.

Take the PostgreSQL archive and its SHA-256 together. Maintain a separate protected manifest mapping the backup and app image to the Django secret, previous Django signing secrets, active and retained payment encryption keys, active and retained MFA keys, database configuration and storage archive versions. Record the secret-store version IDs and retrieval procedure without values. Keep historical key material until affected backups and encrypted rows have expired or been re-encrypted and verified.

Keep the restore network isolated and the web, callbacks, and workers stopped while importing. Point only the recovery shell at the restored database. Disable merchant transport, mail delivery and archive writes. Inject current and retained keys through the secret store. Run `python manage.py check_recovery_keys`, `python manage.py migrate --plan`, and `python manage.py check` before enabling traffic. Validate scoped tenant HTTP access, ledger consistency, original totals, and media checksums. Review pending outbox and verification deliveries before restarting workers. Use a clean Redis service and keep archived callbacks and external jobs fenced.

The [disposable staging fixture](../tests/recovery/staging_fixture.py) runs through `python manage.py shell` with its source passed on standard input and `VEYRA_RECOVERY_MODE=seed` or `verify`. The seed expects an empty disposable database called `veyra_staging`. The verify run uses a separate empty restore destination called `veyra_staging_restored`, supplied through `DATABASE_URL`. Use current test keys with the original keys provided as fallbacks to exercise rotation. Compare both reported media digests and confirm the recorded key counts. The November 26 lab run took 11.42 seconds for the synthetic seed, dump, restore and verification steps. It recovered both tenant rows, the expected order total and identical private media, with one payment bundle and two MFA secrets readable after key rotation. Running the recovery check without the prior payment key returned a failure as expected. Production recovery time and acceptable data loss require separate hosted measurements and approval. [Rehearsal evidence](TESTING.md#priority-0-implementation-2026-11-26) PostgreSQL contains the authoritative data and pending work. Redis carries transient channels and cache. For an authorized business, ``python manage.py requeue_media <business-slug>`` queues missing private archive copies from PostgreSQL. The outbox worker sends them.

A November 24 isolated PostgreSQL 17 rehearsal compared migration and record counts, a physical count and its movement, refund totals, and the SHA-256 of uploaded media. Hosted retention, failover, recovery time and key recovery remain open. [Release evidence](RELEASE-HISTORY.md) · [Operations](OPERATIONS.md)


### Encrypted backup and retention

Use a restricted backup identity and an independent encryption recipient whose private key is available only to authorized recovery operators. Keep backups encrypted in immutable or write-restricted storage. Preserve a signed or independently protected manifest with snapshot time, archive checksum, image digest, storage versions and secret-store version IDs. Record scheduled backup and restore drill results. Never put secret values in the manifest.

Suggested initial objectives for owner approval are a 15-minute data-loss window, a two-hour restore time, 14 daily recovery points and eight weekly recovery points. These are proposed targets. A daily logical dump alone cannot meet the proposed 15-minute objective. Use managed point-in-time recovery or encrypted WAL archiving to meet the approved target. Retain the needed Django, payment and MFA key versions at least as long as any recoverable backup or live ciphertext needs them.

On an authorized Linux backup host with configured PostgreSQL services and the approved OpenPGP recipient, use a protected workspace and stream the database archive to encryption.

~~~bash
set -euo pipefail
umask 077
archive="veyra-$(date -u +%Y%m%dT%H%M%SZ).pgdump.gpg"
pg_dump --format=custom --dbname=service=veyra_backup |
  gpg --batch --encrypt --recipient "$VEYRA_BACKUP_RECIPIENT" --output "$archive.partial"
mv -- "$archive.partial" "$archive"
sha256sum "$archive" > "$archive.sha256"
~~~

From the source application release shell, save a signing marker separately with the protected manifest. This marker grants no application access.

~~~bash
python manage.py shell --no-imports -c \
  'from django.core.signing import dumps; print(dumps("veyra-backup", salt="veyra.backup-probe"))' \
  > backup-signing-probe.txt
~~~

Restore only to the reviewed isolated destination. Check the manifest and run authenticated decryption with the approved recovery identity. Keep workers, mail, provider callbacks and archive writes fenced throughout.

~~~bash
set -euo pipefail
sha256sum --check "$archive.sha256"
gpg --batch --decrypt "$archive" |
  pg_restore --exit-on-error --no-owner --dbname=service=veyra_recovery
python manage.py check_recovery_keys --signing-probe backup-signing-probe.txt
python manage.py migrate --plan
python manage.py check
~~~

Verify access and tenant denial, preserved paid totals, queued work, private media hash and all encryption families before restarting workers. Review restored key revocations against the separate incident record before enabling callbacks. Record backup cutoff and last recoverable commit to measure data loss, then record time from declaration through successful validation. Repeat with a deliberately omitted fallback in the disposable copy to confirm a clear failure. Review hosted archive permissions separately from PostgreSQL media recovery.

## Hosted Stage 1 acceptance

- [ ] Build and record the exact proposed image digest and its approved OS and Python dependency review. On the target run the migration plan, deployment checks and local readiness probe.
- [ ] Record the resolved Compose or host service topology without secret values. Confirm PostgreSQL and Redis bind only to approved private interfaces. On Docker inspect published ports using docker compose ps and inspect the host listeners with ss. From authorized private and external vantage points verify only the proxy serves public traffic.
- [ ] Set exact Django allowed hosts and HTTPS CSRF origins. Configure VEYRA_TRUSTED_PROXY_IPS to the approved direct proxy addresses. Proxy ingress must overwrite forwarding headers from clients and pass the approved Host. Restrict direct access to the ASGI port at the network boundary.
- [ ] From the approved endpoint record HTTPS certificate and redirect behavior, secure cookie and HSTS headers, successful same-origin CSRF, rejected foreign-origin mutation, and an authenticated WSS connection. Confirm a mismatched Host or Origin fails. From an untrusted private test peer confirm a forged forwarded HTTPS header still receives an HTTPS redirect. Record redacted statuses only.
- [ ] Run check_staging_web and check_realtime on the staged image. Check each worker replica by name and identity. Confirm stalled progress and queue backlog send independent alarms and identify who acknowledged recovery.
- [ ] Complete separate provider, backup and mail acceptance. Record which features stay gated for the pilot. [Payment gates](PAYMENTS.md#provider-acceptance) · [Release gates](STAGE-1-RELEASE-READINESS.md#external-release-gates)

Run the probes from the staged Compose environment.

~~~bash
docker compose ps
docker compose exec -T web python manage.py check --deploy
docker compose exec -T web python manage.py check_staging_web
docker compose exec -T web python manage.py check_realtime
docker compose exec -T --index 1 outbox python manage.py check_worker outbox
docker compose exec -T web python manage.py check_operational_queues
curl --fail --show-error --silent --head "https://$STAGE_HOST/healthz/"
curl --show-error --silent --head "https://$STAGE_HOST/healthz/" -H 'Host: outside.invalid'
~~~
