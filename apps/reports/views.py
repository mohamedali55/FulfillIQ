"""Organization-scoped report API endpoints."""

import logging
from django.core.cache import cache
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    OrganizationWarehousePermission,
    accessible_warehouse_ids,
)
from apps.accounts.models import UserProfile
from apps.reports.cache import REPORT_CACHE_TIMEOUT, report_cache_key
from apps.reports.models import ReportExport
from apps.reports.permissions import ReportExportPermission
from apps.reports.queries import (
    FulfillmentPerformanceReport,
    OrderSummaryReport,
    TopProductsReport,
    WarehouseComparisonReport,
)
from apps.reports.serializers import (
    FulfillmentReportQuerySerializer,
    ReportExportCreateSerializer,
    ReportExportSerializer,
    ReportQuerySerializer,
    TopProductsQuerySerializer,
)
from apps.reports.tasks import generate_report_export

logger = logging.getLogger(__name__)


class OrganizationReportView(APIView):
    permission_classes = [IsAuthenticated, OrganizationWarehousePermission]
    query_serializer_class = ReportQuerySerializer
    report_class = None

    def get_report_options(self, request):
        serializer = self.query_serializer_class(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def get_report(self, request):
        options = self.get_report_options(request)
        profile = request.user.profile
        warehouse_ids = accessible_warehouse_ids(profile)
        report = self.report_class(
            organization_id=profile.organization_id,
            warehouse_ids=warehouse_ids,
            **options,
        )
        key = report_cache_key(
            organization_id=profile.organization_id,
            report_name=self.report_class.__name__,
            options=options,
            warehouse_ids=warehouse_ids,
        )
        return cache.get_or_set(key, report.execute, REPORT_CACHE_TIMEOUT)


class OrderSummaryReportView(OrganizationReportView):
    report_class = OrderSummaryReport

    def get(self, request):
        return Response(self.get_report(request))


class FulfillmentPerformanceReportView(OrganizationReportView):
    query_serializer_class = FulfillmentReportQuerySerializer
    report_class = FulfillmentPerformanceReport

    def get(self, request):
        return Response(self.get_report(request))


class WarehouseComparisonReportView(OrganizationReportView):
    report_class = WarehouseComparisonReport

    def get(self, request):
        return Response(self.get_report(request))


class TopProductsReportView(OrganizationReportView):
    query_serializer_class = TopProductsQuerySerializer
    report_class = TopProductsReport

    def get(self, request):
        return Response(self.get_report(request))


class ReportExportListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, ReportExportPermission]
    serializer_class = ReportExportSerializer

    def get_queryset(self):
        profile = self.request.user.profile
        exports = ReportExport.objects.filter(organization_id=profile.organization_id)
        if profile.role not in {UserProfile.Role.ADMIN, UserProfile.Role.MANAGER}:
            exports = exports.filter(requested_by=self.request.user)
        return exports

    def create(self, request, *args, **kwargs):
        input_serializer = ReportExportCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        profile = request.user.profile
        parameters = input_serializer.to_parameters()
        warehouse_ids = accessible_warehouse_ids(profile)
        if (
            parameters.get("warehouse_id") is not None
            and warehouse_ids is not None
            and parameters["warehouse_id"] not in warehouse_ids
        ):
            raise ValidationError(
                {"warehouse_id": "You do not have access to this warehouse."}
            )

        export = ReportExport.objects.create(
            organization=profile.organization,
            requested_by=request.user,
            report_type=input_serializer.validated_data["report_type"],
            parameters=parameters,
        )
        try:
            generate_report_export.delay(export.pk)
        except Exception as exc:
            logger.exception("Could not enqueue report export %s.", export.pk)
            export.status = ReportExport.Status.FAILED
            export.error = f"Report export could not be queued ({type(exc).__name__})."
            export.completed_at = timezone.now()
            export.save(update_fields=["status", "error", "completed_at"])
            raise
        export.refresh_from_db()
        return Response(
            self.get_serializer(export).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ReportExportDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated, ReportExportPermission]
    serializer_class = ReportExportSerializer
    lookup_url_kwarg = "export_id"

    def get_queryset(self):
        profile = self.request.user.profile
        exports = ReportExport.objects.filter(organization_id=profile.organization_id)
        if profile.role not in {UserProfile.Role.ADMIN, UserProfile.Role.MANAGER}:
            exports = exports.filter(requested_by=self.request.user)
        return exports
