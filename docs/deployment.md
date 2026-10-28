# Deployment and recovery

The delivered Compose stack runs on one machine. S8 does not deploy it to a server.
It binds nginx to `127.0.0.1:8080`; PostgreSQL, Redis and the API have no host ports.
Use [the README](../README.md) for the local reviewer run and
[development](development.md) for development processes.

## Included in the stack

- Exact version tags and SHA-256 index digests for base images, with bases for Linux amd64
  and arm64. The recorded runtime checks used arm64. The two whisper.cpp bases pin rolling tags by immutable digest.
- Non-root processes, read-only root filesystems, dropped capabilities and
  `no-new-privileges`. Named volumes hold PostgreSQL, Redis and the Whisper model.
  Temporary writes use tmpfs. Backend services run under Docker's init process.
- Health checks, migration ordering and `unless-stopped` for long-running services.
  The bot has a 40-second stop grace; transcription has 480 seconds; notification
  has 100 seconds. nginx has 5 seconds to stop an open stream.
- Per-service memory limits. Docker rotates each `json-file` log at 10 MB and keeps
  three files. Rotation limits local disk use; it does not provide alerts or history.
- nginx security headers, gzip, immutable hashed assets, fresh HTML and a 64 KB
  API request limit. It forwards SSE without buffering and allows one-hour idle
  upstream reads. An interrupted API connection reaches the browser as a closed
  stream, which causes ticket renewal and reconnect.

## Server work still required

```text
browser --HTTPS--> certificate proxy --HTTP--> web --HTTP--> API
                                                    |         |
Telegram <--long polling--> bot/workers --------> PostgreSQL + Redis
```

Choose a domain, configure DNS and place a certificate-managing reverse proxy in
front of `web`. Open only ports 80 and 443 in the firewall. Keep the application
port on loopback, or restrict its private network to the proxy. Run one bot for a
Telegram token, one instance of each worker role and one API process. The current
Compose file does not provide replicas, failover or a rolling deployment.

For a host proxy, its upstream is `127.0.0.1:8080`. For example, a Caddy site can use
`reverse_proxy 127.0.0.1:8080`; configure its domain and certificates on the server.
[Caddy documents its forwarded headers](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers).

Configure nginx to trust only the proxy's actual address or private subnet:

```nginx
set_real_ip_from <trusted-proxy-address-or-CIDR>;
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```

Do not trust a public address range. See the
[nginx real IP module](https://nginx.org/en/docs/http/ngx_http_realip_module.html).
The local stack forwards `X-Forwarded-Proto $scheme`, which is `http`. When the
server accepts traffic only from the trusted TLS proxy, replace the scheme line in both nginx API locations with
`proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;`. Require the proxy
to overwrite that header. Test it through the full
proxy path before publishing the server.

Set the public dashboard origin for both `bot` and `seed` through a server Compose
override. Their delivered origin is `http://localhost:${WEB_PORT:-8080}`, so changing
only `.env`'s development `WEB_BASE_URL` does not change their container settings.
Use the HTTPS origin without a path. Rebuild nginx after changing its configuration.
Check a dashboard link, an API restart and a fresh SSE connection through HTTPS.

## Credentials

Keep the server `.env` outside version control and limit it to its operator:

```sh
chmod 600 .env
```

The services receive explicit environment allowlists. Docker administrators can
inspect those values. File-based secrets require configuration or adapter support and a secret
store; the delivered application reads environment variables. Use a separate test
bot and provider keys for acceptance.

`POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` default to `taskbot` when
empty. Use non-default values before the first server start. Use letters and digits
because Compose places them in `DATABASE_URL` without URL encoding. Changing them
later does not change an initialized PostgreSQL volume. Rotate database credentials
inside PostgreSQL and update the application settings as one maintenance operation.
`down -v` deletes data and serves only disposable installations or a tested recovery.

The dashboard stores a bearer token in local storage without expiry. A new
`/dashboard` link invalidates older devices. Keep links private and use HTTPS.
The nginx access log omits query strings; upstream error logs and API access logs
can contain consumed SSE tickets. Restrict access to logs and redact credentials
before forwarding or sharing them.

## Back up PostgreSQL

Run from the checkout whose `.env` and Compose project belong to the installation.
Use the same `-p` or `-f` options throughout if the installation overrides them.
The commands read credentials inside the container; they do not embed a password
in the command line.

```sh
umask 077
docker compose exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup.dump
```

Copy the dump off the machine and record the application commit beside it. Encrypt
copies that contain user messages or tokens. Choose a schedule and retention policy
for the installation. Git ignores `*.dump`; git is not backup storage.

## Restore PostgreSQL

Match the application version to the backup first. Stop database writers before
restoring into an existing stack:

```sh
docker compose stop api bot worker-transcribe worker-notify
docker compose exec -T postgres sh -c 'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < backup.dump
docker compose up -d --wait
```

Check the saved tasks and dashboard sign-in after the restart. For a new empty
volume, start PostgreSQL, Redis and migrations before the stop/restore sequence.
The acceptance script uses non-default credentials, takes this custom-format dump,
deletes only its own volumes, starts a fresh stack and restores the same task rows.

Redis is a delivery queue rather than the task record. The worker's recovery sweep
requeues unfinished voice jobs from PostgreSQL. After a voice-job crash, recovery
can wait for the 450–460 second lease per attempt. A PostgreSQL backup does not
preserve a pending Telegram update or a reply that the bot had not committed.

Schedule restore drills on a separate project and record the restored row count.
Keep the production volumes away from these drills.

## Health, resources and logs

Use `docker compose ps` and service logs to investigate failed health checks.
Monitor restarts, OOM kills, queue age, database connections and failed provider
calls. A healthy bot heartbeat means polling works. It does not detect ten handlers
stuck on database locks. A healthy Celery main process alone does not prove that a
pool child works; the acceptance run sends actual jobs and injects a child failure.

S8 measures Linux cgroup `memory.peak` and sets each measured service's limit to at
least twice its peak. The bot's limit is provisional. The shared development
PostgreSQL and Redis limits come from the full verification workload. Measure the
server's own load before changing these values.
The optional multilingual `base` model uses `WHISPER_MEM_LIMIT=1g` by default;
`WHISPER_MODEL=base` is the supported model. Another model needs a pinned checksum,
compatibility checks and new memory measurements.

Measured peaks on 2026-10-27, rounded up to MiB. PostgreSQL and Redis use the
full verification workload; the other measurements use the isolated Docker replay.

| Service | Peak MiB | Limit MiB |
|---|---:|---:|
| PostgreSQL | 234 | 512 |
| Redis | 53 | 128 |
| Migration | 62 | 256 |
| API | 93 | 512 |
| Bot | Owner check on final main | 512 (provisional) |
| Transcription | 193 | 768 |
| Notification | 290 | 1024 |
| Web | 9 | 128 |
| Seed | 63 | 512 |
| Whisper base | 393 | 1024 |

The bot's **512 MiB limit is provisional**. The owner will measure its peak during
the live check on final `main`, after delivery to `main` and `origin/main`, and
confirm or raise the limit to at least twice that peak. The same check covers
Telegram startup and bot stop/drain. The owner approved this deferral on
2026-10-28; these checks did not run in the S8 worktree. The keyless replay observed
15 Redis clients, including the measurement client. Measure again with the bot and
more open tabs.

The application SQL pool ceiling is 25 connections: API 5, bot 5, transcription 5
and notification 10. Migration, seed and measurement clients are separate short-lived
clients. Multiple test checkouts add their own connections to the shared server.
Redis has no client cap in the application; each open dashboard tab holds a stream
and a Redis subscription. Record the observed client count when sizing the server.

Ship logs to an operator-owned destination and add health alerts before exposing
real traffic. Docker log rotation alone does not cover either operation.

## Image and application updates

Review upstream fixes, choose exact release tags and resolve their index digests.
Check both Linux architectures with `docker buildx imagetools inspect`. Update the
Dockerfiles and pulled Compose images together. For whisper.cpp, record the source
revision of both architecture images and retain the model checksum.

Run `yarn verify` and the isolated acceptance script on the candidate commit.
The owner tests live integrations on final `main` after delivery to `main` and
`origin/main`. Keep a backup and then plan the server maintenance window.
Immutable pins require this deliberate update; a restart does not fetch a patched
image under a different digest.

Telegram uses long polling. Keep outbound access to Telegram and the selected
provider. A webhook deployment needs its own HTTPS endpoint and lifecycle design;
S8 does not provide it. Stop the development poller before starting the packaged
bot with the same token.
