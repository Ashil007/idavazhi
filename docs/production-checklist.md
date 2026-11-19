# Production checklist

Every item from the "who's gonna tell vibe coders about…" list, checked
against Idavazhi as of November 2026.

- **Done:** in place and tested.
- **Not needed:** Idavazhi doesn't have the feature the item protects (for
  example, there are no accounts, so no passwords to protect).
- **Later:** worth doing before a wide launch, but not needed for testing
  with friends.

Run the tests with `python -m unittest discover tests`. Run the load test
with `python tools/load_test.py`.

## Security

| Item | Status | How |
|---|---|---|
| HTTPS / TLS | Done | Caddy gets a free certificate and redirects plain `http://` to `https://` (deploy/setup.sh) |
| Security headers | Done | CSP, `nosniff`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` and COOP on every response (transitnav/server.py); HSTS from Caddy |
| Content Security Policy | Done | Only Idavazhi's own scripts; no inline scripts. Tested: the map, plans and the offline cache all work under it |
| Clickjacking | Done | `frame-ancestors 'none'` plus `X-Frame-Options: DENY` |
| XSS | Done | Every name from riders, OpenStreetMap or the timetable goes through `escapeHtml` before it reaches the page; CSP blocks injected scripts as a second line of defence |
| CSRF | Done | Posts must be JSON, and when the browser names the page it came from, it must be Idavazhi's own site (`_same_site`) |
| SQL injection | Done | Every query uses `?` parameters (timings.py, stopnames.py, places.py) |
| Path traversal | Done | Files are served only from `web/`; checked with `commonpath`. Tested with `../`, `%2e%2e` and `data/` |
| Command injection | Not needed | Nothing a visitor sends reaches a shell |
| SSRF | Not needed | The server only contacts fixed addresses (Overpass, the route servers, Nominatim), never one a visitor gives |
| Open redirects | Done | The only redirects go to Idavazhi's own `/app` |
| Input validation (on the server) | Done | Coordinates must be finite numbers on the globe; travel modes must be on a list; search text is capped at 120 characters, ids at 64, bodies at 4 KB (and 64 KB at Caddy) |
| File upload security | Not needed | There are no uploads |
| Mass assignment | Not needed | Only named fields are read from each post |
| IDOR | Not needed | There are no private records; bus times and stop names are public, and votes are tied to a hashed device id |
| Rate limiting | Done | Per visitor: plans, searches, prefetches, bus times and stop edits (transitnav/limits.py). Over the limit gets a polite message |
| Brute force, account lockout, passwords, password resets, magic links, passkeys, 2FA, SSO, sessions, cookie flags, tokens | Not needed | Idavazhi has no accounts, logins or cookies |
| Authentication / authorization / roles / row-level security | Not needed | Same reason. Becomes "Later" if riders ever sign in |
| API keys, secrets management, secret rotation, environment variables | Done | The app holds no secrets. The only key is your SSH key, which stays on your PC (`~/.ssh/vazhi-cloud.key`, locked to your user). Settings go through environment variables (`VAZHI_MAX_PLANS`, `TRANSITNAV_VALHALLA`) |
| Encryption in transit | Done | HTTPS between phones and the server; everything else is inside one machine |
| Encryption at rest | Later | The disk is Oracle's (encrypted by Oracle). The riders' data has no personal details |
| Hashing vs encryption, salting | Done | Device ids are stored as a salted SHA-256 hash (`timings._device`), never as given |
| Dependency vulnerabilities | Done | Python uses only its standard library, apart from pyosmium and shapely for building the map. The three map libraries are pinned versions from npm, integrity-checked, and served from `web/vendor` (no outside CDN at run time) |
| Principle of least privilege | Done | The app runs as its own `vazhi` user with a read-only system (`ProtectSystem`, `NoNewPrivileges`), writing only to `/opt/vazhi` |
| Penetration testing | Later | Ask a friend in security to try it, or run OWASP ZAP against the cloud link |
| Audit logs | Done (small) | One log line per trip plan (kind, outcome, time taken) and per rider contribution; never where people go |

## Reliability and operations

| Item | Status | How |
|---|---|---|
| Error handling | Done | Visitors see a friendly message; the details go to the server log only |
| HTTP status codes | Done | 400 for bad input, 403 for cross-site posts, 404, 413 for oversized posts, 429 for too many requests (with `Retry-After`), 503 when busy |
| Timeouts | Done | Silent connections are dropped after 60 s; every outside call has its own timeout |
| Exponential backoff, circuit breakers | Done | Busy Overpass mirrors and route servers are put aside for a cool-down and retried later (overpass.py, router.py) |
| Idempotency | Done | Votes use `INSERT OR REPLACE` per device, so sending one twice counts once |
| Race conditions, concurrency | Done | Database writes hold a lock; at most 2 plans run per copy of the app; the rest wait in a queue |
| Graceful shutdown | Done | The app stops cleanly on SIGTERM (tested: "Shutting down." on `systemctl restart`) |
| Health checks | Done | `/api/health` (GET or HEAD). Caddy uses it to skip a copy that stops answering |
| Uptime monitoring, alerting | Later | Point a free monitor (e.g. UptimeRobot) at `https://<ip>.sslip.io/api/health` |
| Logging, structured logging | Done (simple) | One timestamped line per event, in the system journal (`journalctl -u vazhi`) |
| Error tracking, distributed tracing | Later | Tracebacks are in the journal. Add Sentry when there are real users |
| Monitoring, cost monitoring | Later | Set a budget alert of ₹1 in the Oracle console, so you hear if anything ever costs money |
| Backups | Done | Riders' data is backed up every night, with 14 days kept and readable by root only (`/var/backups/vazhi`) |
| Disaster recovery | Done | `deploy/push.py` rebuilds a whole server from nothing; restore riders' data from the backups |
| Load testing | Done | `tools/load_test.py`; results below |
| Caching | Done | Map tiles cached for a day; files revalidated with 304s; searches cached for 10 min; routes, track graphs and geocoding results are kept in memory, all with size limits |
| CDNs | Later | Not needed for Kerala-sized traffic; Cloudflare in front would be the next step |
| Load balancing, horizontal vs vertical scaling, stateless services | Done | One copy of the app per processor core behind Caddy (`least_conn`). The copies share nothing but the database files |
| Reverse proxies | Done | Caddy |
| API gateways, message queues, webhooks, read replicas, connection pooling | Not needed | One small app on one machine; revisit at a much larger scale |
| Background jobs, cron jobs | Done | Map preloading and the place index run in the background; backups run from cron |
| Cache invalidation | Done | New offline-cache version on every app change (`sw.js`); map tiles expire after a month |
| Memory leaks | Done | Every in-memory cache has a size limit; rate-limit records of past visitors are swept away |
| Database indexing, migrations, normalization, soft deletes | Done (small) | Primary keys on every table; the tables are created if missing |
| Docker, Infrastructure as Code | Done | The route service and map builder run in Docker; the whole server is described by `deploy/setup.sh` |
| CI/CD, staging environments | Later | No GitHub repo yet. Your PC (with the phone link) is the staging copy; the cloud server is production |
| Feature flags | Not needed | Not yet |

## Code and product

| Item | Status | How |
|---|---|---|
| Automated testing (unit, integration, end-to-end) | Done (start) | 19 tests: server safety rules, bus-line trimming, train-track tracing, rate limits |
| Debugging, profiling, reading error messages | Done | The load test found the search slowdown; profiling showed the cause |
| Time zones | Done | Bus times are worked out on the phone in its own time; the server runs on India time (`TZ=Asia/Kolkata`) |
| Character encoding | Done | UTF-8 everywhere, including Malayalam names |
| Async / await | Done | The browser code uses promises; the server uses threads |
| Mobile responsiveness | Done | Phone-first layout (bottom sheet, trip drawer) |
| Accessibility | Done (partly) | Labelled buttons and switches, keyboard focus, reduced-motion support. Later: a full screen-reader pass |
| Performance budgets | Done (partly) | Large files gzipped; the map shell is cached offline |
| Data privacy (GDPR; for India the DPDP Act 2023) | Done (partly) | Locations are never stored or logged, and network addresses are kept in memory only for rate limits. Later: a privacy page on the website |
| Code review, branching, semantic versioning, API versioning, API docs | Later | Worth setting up with GitHub before others work on the code |
| Technical debt | Ongoing | The 2016 train timetable is the biggest item |

## Load test (November 2026)

Run on the development PC (12 cores). Each pretend rider opened the app,
loaded 12 map tiles, searched, and planned a bus trip every 10 seconds.
That's much heavier than a real person.

| Setup | Riders | Requests a second | Search (median) | Trip plans a minute |
|---|---|---|---|---|
| One copy | 50 | 92 | 3.1 s | 180 |
| One copy | 150 | 60 (errors) | 5.8 s | 160 |
| Four copies (what the server runs) | 150 | 135 | under 0.25 s | about 190 (some "busy") |

The cloud server's free tier gives 2–4 slower ARM cores. Expect roughly
100–150 trip plans a minute and around 100 people using the app at the same
moment. That's a few thousand people a day, because most people use it for
a minute or two. The next limit is planning itself; past that, the step is
more machines, or moving planning into separate worker processes.
