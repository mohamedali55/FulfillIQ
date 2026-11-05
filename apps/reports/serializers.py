"""Input validation for report query parameters."""

from rest_framework import serializers

from apps.reports.models import ReportExport


class ReportQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    warehouse_id = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError(
                {"end_date": "Must be on or after start_date."}
            )
        return attrs


class FulfillmentReportQuerySerializer(ReportQuerySerializer):
    sla_days = serializers.IntegerField(required=False, min_value=1, default=7)


class TopProductsQuerySerializer(ReportQuerySerializer):
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=10)


class ReportExportCreateSerializer(serializers.Serializer):
    report_type = serializers.ChoiceField(choices=ReportExport.ReportType.choices)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    warehouse_id = serializers.IntegerField(required=False, min_value=1)
    sla_days = serializers.IntegerField(required=False, min_value=1, default=7)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=10)

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError(
                {"end_date": "Must be on or after start_date."}
            )
        return attrs

    def to_parameters(self):
        report_type = self.validated_data["report_type"]
        allowed = {
            ReportExport.ReportType.SUMMARY: {"start_date", "end_date", "warehouse_id"},
            ReportExport.ReportType.FULFILLMENT: {
                "start_date",
                "end_date",
                "warehouse_id",
                "sla_days",
            },
            ReportExport.ReportType.WAREHOUSES: {"start_date", "end_date"},
            ReportExport.ReportType.TOP_PRODUCTS: {
                "start_date",
                "end_date",
                "warehouse_id",
                "limit",
            },
        }[report_type]
        values = {
            key: value
            for key, value in self.validated_data.items()
            if key in allowed
        }
        for key in ("start_date", "end_date"):
            if key in values:
                values[key] = values[key].isoformat()
        return values


class ReportExportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportExport
        fields = [
            "id",
            "report_type",
            "parameters",
            "status",
            "result",
            "error",
            "created_at",
            "completed_at",
        ]
        read_only_fields = fields
