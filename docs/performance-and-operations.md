# Performance and operations

## PostgreSQL benchmarks

Keep the regular development and test database on SQLite. PostgreSQL-specific
profiling uses `config.settings.performance`:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.performance"
$env:FULFILLIQ_DB_PASSWORD = "<local PostgreSQL password>"
python manage.py migrate
python manage.py seed_data --orders 1000000 --customers 50000
python manage.py benchmark_reports --organization acme-corp --iterations 5 --output benchmarks\reports.json
```

The benchmark command requires PostgreSQL, records report timings and includes
`EXPLAIN ANALYZE` plans with buffer statistics for recent-order and
delivered-order access patterns. Run it before and after adding candidate
indexes; retain both JSON outputs and compare medians and plan choices. Do not
apply an index solely because it appears plausible—the query plan should show
the target scan is improved without unacceptable write/storage cost.

### Local benchmark results

Measured on PostgreSQL 17 with 1,000,000 orders, 50,000 customers, 91
distinct order dates, and 4,149,301 status-history rows. Each result is the
median of three runs over a 30-day window.

| Report | Baseline (ms) | Optimized (ms) | Change |
|---|---:|---:|---:|
| Order summary | 1,976.985 | 1,162.894 | 41.2% faster |
| Warehouse comparison | 1,195.430 | 861.305 | 28.0% faster |
| Top products | 87,175.121 | 21,320.021 | 75.5% faster |
| Fulfillment | 30,937.211 | 34,966.800 | 13.0% slower |

The top-products aggregation now avoids repeatedly joining each product to
every candidate order item. Date ranges use inclusive local start dates and
exclusive next-day end bounds. The warehouse/date composite index is
`idx_order_wh_created`; the plan still prefers the existing warehouse index
for some sorted scans. No partial index was added without plan evidence.
Fulfillment remains a streaming scan over large order/history sets; use the
asynchronous export path for expensive uncached fulfillment reports.

The baseline, intermediate, and final JSON results are saved under
`benchmarks\milestone5_*.json`.

The seed command is deterministic by seed and supports large datasets. Do not
use `--clear` on a database with data you need; it removes all order-domain
records and organizations.

## Redis and Celery

Production settings use `REDIS_URL` for the Django cache and default Celery
broker/result URLs. `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` can override
those endpoints independently. Supply these values through the deployment
environment; do not commit credentials.

Development and tests use the in-memory cache and eager Celery execution.
Production runs require a reachable Redis service and a separate Celery worker:

```powershell
celery -A config worker --loglevel=INFO
```

Report GET responses are cached for five minutes. Order creation, status
transitions, and total recalculation advance the affected organization's
database-backed report-cache generation in the same transaction. Report
exports are persisted and can be submitted at
`POST /api/v1/reports/exports/`; poll their resource URL for completion.

## Authorization and throttling

All order and report APIs require an active organization profile. Admin and
manager roles have organization-wide warehouse access and may mutate orders.
Analyst and viewer roles have read-only access to explicitly assigned
warehouses; analysts may also submit report exports. The default API throttles
are 30 anonymous requests per hour and 1,000 authenticated requests per day.

Create `UserWarehouseAccess` grants only within the profile's organization.
The model validates this invariant in forms; the authorization queryset also
filters out mismatched grants.

## HTTP load test

Start the application against the database/cache configuration being measured,
then set `LOAD_TEST_USERNAME` and `LOAD_TEST_PASSWORD` in the shell environment:

```powershell
python scripts\load_test.py --base-url http://127.0.0.1:8000 --path /api/v1/reports/summary/ --workers 10 --duration 30
```

The script authenticates with JWT and reports request throughput, failures,
median latency, and p95/p99 latency. Run it against a representative dataset
and record machine, database, cache, worker, and concurrency settings with the
results.

Local verification against PostgreSQL 17 with the in-memory development cache
completed 542 requests in 15 seconds at four workers, with zero failures
(35.95 requests/second; median 98.57 ms, p95 204.26 ms, p99 238.54 ms).
Redis and a separate Celery worker were not installed, so live production
cache/broker integration still needs deployment-level verification.
