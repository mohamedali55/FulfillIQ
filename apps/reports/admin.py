from django.contrib import admin

from apps.reports.models import ReportExport


@admin.register(ReportExport)
class ReportExportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "report_type",
        "organization",
        "requested_by",
        "status",
        "created_at",
        "completed_at",
    )
    list_filter = ("report_type", "status", "organization")
    search_fields = ("requested_by__username",)
    readonly_fields = (
        "organization",
        "requested_by",
        "report_type",
        "parameters",
        "status",
        "result",
        "error",
        "created_at",
        "completed_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
