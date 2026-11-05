# FulfillIQ

### Enterprise Order Intelligence & Reporting API

FulfillIQ helps teams manage orders and understand how fulfillment is going.
It’s a multi-tenant API built with Django and Django REST Framework, with
organization-aware access, order lifecycle management, and operational reports.

## What it includes

- JWT authentication and organization- and warehouse-scoped permissions
- Order creation, filtering, status transitions, and history
- Summary, fulfillment, warehouse, and product reports
- Cached reports and asynchronous report exports
- PostgreSQL performance benchmarks and synthetic data tools

## How it works

1. **Sign in:** A client exchanges its username and password for JWT tokens.
   Each API user is linked to an organization and a role.
2. **Access the right data:** Every order and report request is scoped to the
   user's organization. Analysts and viewers are further limited to assigned
   warehouses; administrators and managers can work across their organization.
3. **Manage orders:** The API validates the customer, warehouse, and products,
   then creates the order, its line items, calculated total, and initial
   history entry together in a database transaction. Status changes follow
   allowed lifecycle transitions and are recorded in the order history.
4. **Understand fulfillment:** Report endpoints calculate order, fulfillment,
   warehouse, and product metrics for the permitted data. Results are cached;
   order changes advance the organization's cache version so stale reports
   aren't reused.
5. **Export larger reports:** A client submits an export request and receives
   an export ID to check later. Celery generates the report and saves its
   status and result. Development runs tasks eagerly; production uses a
   separate Celery worker and Redis.

## Get started

Local development uses SQLite, so Redis isn’t needed to get the API running.
From the project directory, run these commands in PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements\dev.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The API is available at `http://127.0.0.1:8000/api/v1/`. To access it, a user
needs an active profile linked to an organization. Create profiles and manage
warehouse access in Django admin at `/admin/`.

## API at a glance

Get JWT tokens from `POST /api/v1/auth/token/`, then send the access token as a
Bearer token.

| Endpoint | What it does |
|---|---|
| `/api/v1/orders/` | List, filter, and create orders |
| `/api/v1/orders/{order_number}/` | View order details |
| `/api/v1/orders/{order_number}/transition/` | Change order status |
| `/api/v1/orders/{order_number}/history/` | View status history |
| `/api/v1/reports/summary/` | Order totals and trends |
| `/api/v1/reports/fulfillment/` | Fulfillment performance |
| `/api/v1/reports/warehouses/` | Compare warehouses |
| `/api/v1/reports/top-products/` | Product performance |
| `/api/v1/reports/exports/` | Submit or check report exports |

## Tests

```powershell
pytest
```

The project has 53 passing tests. For PostgreSQL benchmark results and
operational notes, see
[`docs/performance-and-operations.md`](docs/performance-and-operations.md).

## Production notes

Production uses PostgreSQL, Redis, and Celery. Set `DJANGO_SECRET_KEY`,
`ALLOWED_HOSTS`, database credentials, and Redis/Celery URLs through the
deployment environment—never commit secrets. Live Redis and Celery-worker
integration still needs verification before deployment.
