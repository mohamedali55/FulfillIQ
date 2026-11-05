"""Persistent state for asynchronous report exports."""

from django.conf import settings
from django.db import models

from apps.accounts.models import Organization


class ReportExport(models.Model):
    class ReportType(models.TextChoices):
        SUMMARY = "summary", "Order summary"
        FULFILLMENT = "fulfillment", "Fulfillment performance"
        WAREHOUSES = "warehouses", "Warehouse comparison"
        TOP_PRODUCTS = "top_products", "Top products"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="report_exports",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="report_exports",
    )
    report_type = models.CharField(max_length=20, choices=ReportType.choices)
    parameters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "requested_by", "created_at"],
                name="idx_export_org_user_date",
            ),
        ]

    def __str__(self):
        return f"{self.report_type} export #{self.pk} ({self.status})"
