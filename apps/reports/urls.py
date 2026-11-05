"""Versioned reporting API routes."""

from django.urls import path

from apps.reports.views import (
    FulfillmentPerformanceReportView,
    OrderSummaryReportView,
    ReportExportDetailView,
    ReportExportListCreateView,
    TopProductsReportView,
    WarehouseComparisonReportView,
)

urlpatterns = [
    path("summary/", OrderSummaryReportView.as_view(), name="report-summary"),
    path(
        "fulfillment/",
        FulfillmentPerformanceReportView.as_view(),
        name="report-fulfillment",
    ),
    path(
        "warehouses/",
        WarehouseComparisonReportView.as_view(),
        name="report-warehouses",
    ),
    path("top-products/", TopProductsReportView.as_view(), name="report-top-products"),
    path("exports/", ReportExportListCreateView.as_view(), name="report-exports"),
    path(
        "exports/<int:export_id>/",
        ReportExportDetailView.as_view(),
        name="report-export-detail",
    ),
]
