# Deferred scale/reliability items (> $10/month Azure spend)

Owner rule (2026-07-02): scalability/reliability work that would require **new
or expensive (> $10/month) Azure spend** is deferred here rather than executed.
Revisit when traffic justifies the line item.

## From the 2026-07-02 deep review

The 5-lens adversarial deep review (see `DEEP-REVIEW-PUNCHLIST-2026-07-02.md`)
produced **zero** findings requiring new Azure spend — all 35 confirmed items
were code/test/config changes within existing infrastructure ($0/mo), and all
have been executed or staged in PRs #63–#73.

## Known future-scale candidates (carried from prior audits + this session's changes)

| # | Item | Trigger to revisit | Est. cost | Notes |
|---|------|--------------------|-----------|-------|
| D1 | **CDN in front of `/api/v1/media/{id}`** (Azure Front Door Standard or CF proxy on the API host) | Media egress or container CPU visibly driven by image serving. NEW RELEVANCE: as of 2026-07-02 all ~3,100 character/answer images are rehosted into Postgres `media_assets` and served by the Container App — durable, but every image request now hits the API + DB. Immutable-cache headers are already set, so browsers/CF edge (if proxied) absorb most repeats. | Front Door ~$35/mo + egress; $0 if Cloudflare proxying is extended to the API host (config-only — consider FIRST) | The $0 Cloudflare option should be tried before any paid CDN. |
| D2 | **Redis tier with persistence/replica** (Azure Cache Basic → Standard) | Session-loss complaints or eviction storms; today a Redis loss is survivable (P9: /status,/proceed,/next all rehydrate from Postgres) | ~$40+/mo delta | Rehydrate path (PR #67) makes this genuinely deferrable. |
| D3 | **Postgres tier bump** (B-series burstable → GP) for `max_connections` ≥ pool 60 + image-serving load | Connection exhaustion warnings, p95 query latency growth; statement_timeout (PR #67) caps worst-case queries | ~$50+/mo delta | Owner punch-list already tracks verifying `max_connections` vs pool on the current tier ($0). |
| D4 | **Container Apps min-replicas ≥ 1 always-warm** (avoid cold starts) | Cold-start complaints on first quiz of the morning; the FE has a 30s escape hatch + warm-up on boot today | ~$15–30/mo depending on cpu/mem | Measure real cold-start frequency first (Log Analytics query documented in launch punch list). |
| D5 | **Log Analytics ingestion growth** | Ingestion > free tier as traffic grows (structured logs on hot paths) | ~$2.76/GB beyond cap | Consider sampling poll-endpoint logs before paying. |
| D6 | **X/Twitter API Basic tier for the social bot reply pipeline** | Owner decides replies are worth it; posts-only mode works on the free tier | **$200/mo (external, not Azure)** | Documented in the social-agent README + human punch list (PR #64). |

None of these gate launch. D1's $0 Cloudflare variant is the only one worth
evaluating in the first month.
