"""Celery tasks for asynchronous report generation."""

import json
import logging
from datetime import date

from celery import shared_task
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from apps.accounts.permissions import accessible_warehouse_ids
from apps.reports.models import ReportExport
from apps.reports.queries import (
    FulfillmentPerformanceReport,
    OrderSummaryReport,
    TopProductsReport,
    WarehouseComparisonReport,
)

logger = logging.getLogger(__name__)


REPORTS = {
    ReportExport.ReportType.SUMMARY: OrderSummaryReport,
    ReportExport.ReportType.FULFILLMENT: FulfillmentPerformanceReport,
    ReportExport.ReportType.WAREHOUSES: WarehouseComparisonReport,
    ReportExport.ReportType.TOP_PRODUCTS: TopProductsReport,
}


@shared_task(bind=True, name="reports.generate_export")
def generate_report_export(self, export_id):
    export = ReportExport.objects.select_related(
        "organization", "requested_by__profile"
    ).get(pk=export_id)
    export.status = ReportExport.Status.RUNNING
    export.error = ""
    export.save(update_fields=["status", "error"])

    try:
        if not export.organization.is_active:
            raise ValueError("The export organization is inactive.")
        profile = export.requested_by.profile
        if profile.organization_id != export.organization_id:
            raise ValueError("The export requester no longer belongs to this organization.")

        report_class = REPORTS[export.report_type]
        parameters = dict(export.parameters)
        for field in ("start_date", "end_date"):
            if parameters.get(field) is not None:
                parameters[field] = date.fromisoformat(parameters[field])
        report = report_class(
            organization_id=export.organization_id,
            warehouse_ids=accessible_warehouse_ids(profile),
            **parameters,
        )
        result = report.execute()
        export.result = json.loads(json.dumps(result, cls=DjangoJSONEncoder))
        export.status = ReportExport.Status.COMPLETED
        export.completed_at = timezone.now()
        export.save(update_fields=["result", "status", "completed_at"])
        return export.pk
    except Exception as exc:
        logger.exception("Report export %s failed.", export.pk)
        export.status = ReportExport.Status.FAILED
        export.error = f"Report generation failed ({type(exc).__name__})."
        export.completed_at = timezone.now()
        export.save(update_fields=["status", "error", "completed_at"])
        raise
