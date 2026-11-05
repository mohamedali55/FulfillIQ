"""Measure PostgreSQL report timings and capture EXPLAIN ANALYZE plans."""

import json
import statistics
import time
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.accounts.models import Organization
from apps.orders.models import Order, OrderStatus
from apps.reports.queries import (
    FulfillmentPerformanceReport,
    OrderSummaryReport,
    TopProductsReport,
    WarehouseComparisonReport,
)


class Command(BaseCommand):
    help = "Benchmark reporting queries on PostgreSQL and capture query plans."

    def add_arguments(self, parser):
        parser.add_argument("--organization", default="acme-corp", help="Organization slug.")
        parser.add_argument("--iterations", type=int, default=5)
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--output", help="Optional path to save the JSON report.")
        parser.add_argument(
            "--include-fulfillment",
            action="store_true",
            help="Include the memory-intensive stage-duration report.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError(
                "This command requires PostgreSQL. Run it with "
                "DJANGO_SETTINGS_MODULE=config.settings.performance."
            )
        if options["iterations"] < 1 or options["days"] < 1:
            raise CommandError("--iterations and --days must be positive.")

        try:
            organization = Organization.objects.get(slug=options["organization"])
        except Organization.DoesNotExist as exc:
            raise CommandError(
                f"Organization '{options['organization']}' does not exist."
            ) from exc

        warehouse_id = (
            Order.objects.filter(warehouse__organization=organization)
            .values_list("warehouse_id", flat=True)
            .first()
        )
        since = timezone.now() - timedelta(days=options["days"])
        reports = {
            "order_summary": OrderSummaryReport(
                organization_id=organization.pk,
                start_date=since.date(),
            ),
            "warehouse_comparison": WarehouseComparisonReport(
                organization_id=organization.pk,
                start_date=since.date(),
            ),
            "top_products": TopProductsReport(
                organization_id=organization.pk,
                start_date=since.date(),
            ),
        }
        if options["include_fulfillment"]:
            reports["fulfillment"] = FulfillmentPerformanceReport(
                organization_id=organization.pk,
                start_date=since.date(),
            )

        plans = {}
        if warehouse_id is not None:
            plans["warehouse_recent_orders"] = (
                Order.objects.filter(
                    warehouse__organization=organization,
                    warehouse_id=warehouse_id,
                    created_at__gte=since,
                )
                .order_by("-created_at")
                .explain(analyze=True, buffers=True, format="text")
            )
            plans["warehouse_delivered_orders"] = (
                Order.objects.filter(
                    warehouse__organization=organization,
                    warehouse_id=warehouse_id,
                    status__in=[OrderStatus.DELIVERED, OrderStatus.RETURNED],
                    created_at__gte=since,
                )
                .order_by("-created_at")
                .explain(analyze=True, buffers=True, format="text")
            )

        timing_results = {}
        for name, report in reports.items():
            samples = []
            for _ in range(options["iterations"]):
                started = time.perf_counter()
                report.execute()
                samples.append((time.perf_counter() - started) * 1000)
            timing_results[name] = {
                "iterations": options["iterations"],
                "median_ms": round(statistics.median(samples), 3),
                "min_ms": round(min(samples), 3),
                "max_ms": round(max(samples), 3),
                "samples_ms": [round(sample, 3) for sample in samples],
            }

        result = {
            "database": connection.settings_dict["NAME"],
            "organization": organization.slug,
            "order_count": Order.objects.filter(
                warehouse__organization=organization
            ).count(),
            "days": options["days"],
            "reports": timing_results,
            "query_plans": plans,
        }
        rendered = json.dumps(result, indent=2)
        self.stdout.write(rendered)
        if options["output"]:
            output_path = Path(options["output"])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Saved benchmark to {output_path}"))
