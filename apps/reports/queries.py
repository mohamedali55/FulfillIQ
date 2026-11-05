"""
Report query builders for organization-scoped order reporting.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Avg, Case, Count, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Product,
)


class OrderReport:
    """Shared date and tenant filters for order reports."""

    def __init__(
        self,
        *,
        organization_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        warehouse_id: int | None = None,
        warehouse_ids: list[int] | None = None,
    ):
        self.organization_id = organization_id
        self.start_date = start_date
        self.end_date = end_date
        self.warehouse_id = warehouse_id
        self.warehouse_ids = warehouse_ids

    def _base_queryset(self):
        qs = self._apply_date_range(
            Order.objects.filter(
                warehouse__organization_id=self.organization_id
            ),
            field_name="created_at",
        )
        if self.warehouse_id is not None:
            qs = qs.filter(warehouse_id=self.warehouse_id)
        if self.warehouse_ids is not None:
            qs = qs.filter(warehouse_id__in=self.warehouse_ids)
        return qs

    def _apply_date_range(self, queryset, *, field_name: str):
        current_timezone = timezone.get_current_timezone()
        if self.start_date:
            start_at = timezone.make_aware(
                datetime.combine(self.start_date, time.min),
                current_timezone,
            )
            queryset = queryset.filter(**{f"{field_name}__gte": start_at})
        if self.end_date:
            end_exclusive = timezone.make_aware(
                datetime.combine(self.end_date + timedelta(days=1), time.min),
                current_timezone,
            )
            queryset = queryset.filter(**{f"{field_name}__lt": end_exclusive})
        return queryset


class OrderSummaryReport(OrderReport):
    """Order totals, status breakdown, revenue, and daily metrics."""

    def get_summary(self) -> dict:
        return self._base_queryset().aggregate(
            total_orders=Count("id"),
            total_revenue=Coalesce(Sum("total_amount"), Value(Decimal("0.00"))),
            avg_order_value=Coalesce(Avg("total_amount"), Value(Decimal("0.00"))),
        )

    def get_status_breakdown(self) -> list[dict]:
        return list(
            self._base_queryset()
            .values("status")
            .annotate(count=Count("id"), revenue=Sum("total_amount"))
            .order_by("status")
        )

    def get_daily_orders(self) -> list[dict]:
        return list(
            self._base_queryset()
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"), revenue=Sum("total_amount"))
            .order_by("date")
        )

    def execute(self) -> dict:
        return {
            "summary": self.get_summary(),
            "by_status": self.get_status_breakdown(),
            "daily": self.get_daily_orders(),
        }


class FulfillmentPerformanceReport(OrderReport):
    """Fulfillment stage counts, durations, rates, and delivery SLA."""

    DEFAULT_SLA_DAYS = 7
    STAGES = (
        ("pending_to_confirmed", OrderStatus.CONFIRMED),
        ("confirmed_to_processing", OrderStatus.PROCESSING),
        ("processing_to_shipped", OrderStatus.SHIPPED),
        ("shipped_to_delivered", OrderStatus.DELIVERED),
    )

    def __init__(self, *, sla_days: int = DEFAULT_SLA_DAYS, **kwargs):
        if sla_days < 1:
            raise ValueError("sla_days must be at least 1.")
        super().__init__(**kwargs)
        self.sla_days = sla_days

    def get_fulfillment_funnel(self) -> dict:
        return self._base_queryset().aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status=OrderStatus.PENDING)),
            confirmed=Count("id", filter=Q(status=OrderStatus.CONFIRMED)),
            processing=Count("id", filter=Q(status=OrderStatus.PROCESSING)),
            shipped=Count("id", filter=Q(status=OrderStatus.SHIPPED)),
            delivered=Count("id", filter=Q(status=OrderStatus.DELIVERED)),
            cancelled=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
            returned=Count("id", filter=Q(status=OrderStatus.RETURNED)),
        )

    def get_completion_rates(self) -> dict:
        totals = self._base_queryset().aggregate(
            total=Count("id"),
            delivered=Count("id", filter=Q(status=OrderStatus.DELIVERED)),
            cancelled=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
            returned=Count("id", filter=Q(status=OrderStatus.RETURNED)),
        )
        total = totals["total"]
        return {
            "completion_rate": round(totals["delivered"] / total * 100, 2) if total else 0.0,
            "cancellation_rate": round(totals["cancelled"] / total * 100, 2) if total else 0.0,
            "return_rate": round(totals["returned"] / total * 100, 2) if total else 0.0,
        }

    def get_stage_durations(self) -> dict:
        """Average elapsed hours for each completed transition stage."""
        orders = self._base_queryset().order_by("id").values_list("id", "created_at")
        history = iter(
            OrderStatusHistory.objects.filter(
                order_id__in=self._base_queryset().values("id"),
                new_status__in=[status for _, status in self.STAGES],
            )
            .order_by("order_id", "changed_at")
            .values_list("order_id", "new_status", "changed_at")
            .iterator(chunk_size=2000)
        )
        current_history = next(history, None)
        durations_by_stage = {
            name: {"total_hours": 0.0, "sample_size": 0}
            for name, _ in self.STAGES
        }

        for order_id, created_at in orders.iterator(chunk_size=2000):
            reached_at = {}
            while current_history and current_history[0] == order_id:
                _, status, changed_at = current_history
                reached_at.setdefault(status, changed_at)
                current_history = next(history, None)

            for index, (stage_name, next_status) in enumerate(self.STAGES):
                previous_status = (
                    None if index == 0 else self.STAGES[index - 1][1]
                )
                end_at = reached_at.get(next_status)
                start_at = created_at if previous_status is None else reached_at.get(previous_status)
                if start_at is not None and end_at is not None and end_at >= start_at:
                    durations_by_stage[stage_name]["total_hours"] += (
                        end_at - start_at
                    ).total_seconds() / 3600
                    durations_by_stage[stage_name]["sample_size"] += 1

        return {
            stage_name: {
                "average_hours": (
                    round(values["total_hours"] / values["sample_size"], 2)
                    if values["sample_size"]
                    else None
                ),
                "sample_size": values["sample_size"],
            }
            for stage_name, values in durations_by_stage.items()
        }

    def get_sla_compliance(self) -> dict:
        """Classify delivered orders using their actual delivery-history time."""
        delivered_orders = self._base_queryset().filter(
            status__in=[OrderStatus.DELIVERED, OrderStatus.RETURNED]
        ).order_by("id").values_list("id", "created_at")
        history = iter(
            OrderStatusHistory.objects.filter(
                order_id__in=self._base_queryset()
                .filter(status__in=[OrderStatus.DELIVERED, OrderStatus.RETURNED])
                .values("id"),
                new_status=OrderStatus.DELIVERED,
            )
            .order_by("order_id", "changed_at")
            .values_list("order_id", "changed_at")
            .iterator(chunk_size=2000)
        )
        current_history = next(history, None)
        on_time = delayed = missing_timestamp = delivered_count = 0

        for order_id, created_at in delivered_orders.iterator(chunk_size=2000):
            delivered_count += 1
            delivery_time = None
            while current_history and current_history[0] == order_id:
                _, changed_at = current_history
                if delivery_time is None:
                    delivery_time = changed_at
                current_history = next(history, None)

            if delivery_time is None:
                missing_timestamp += 1
            elif delivery_time <= created_at + timedelta(days=self.sla_days):
                on_time += 1
            else:
                delayed += 1

        measured = on_time + delayed
        return {
            "sla_days": self.sla_days,
            "delivered_orders": delivered_count,
            "measured_deliveries": measured,
            "missing_delivery_timestamp": missing_timestamp,
            "on_time": on_time,
            "delayed": delayed,
            "on_time_rate": round(on_time / measured * 100, 2) if measured else 0.0,
        }

    def execute(self) -> dict:
        return {
            "funnel": self.get_fulfillment_funnel(),
            "rates": self.get_completion_rates(),
            "stage_durations": self.get_stage_durations(),
            "sla_compliance": self.get_sla_compliance(),
        }


class WarehouseComparisonReport(OrderReport):
    """Compare order volume, revenue, and completion rates by warehouse."""

    def get_warehouse_metrics(self) -> list[dict]:
        return list(
            self._base_queryset()
            .values("warehouse__id", "warehouse__code", "warehouse__name", "warehouse__city")
            .annotate(
                total_orders=Count("id"),
                total_revenue=Coalesce(Sum("total_amount"), Value(Decimal("0.00"))),
                avg_order_value=Coalesce(Avg("total_amount"), Value(Decimal("0.00"))),
                delivered_count=Count("id", filter=Q(status=OrderStatus.DELIVERED)),
                cancelled_count=Count("id", filter=Q(status=OrderStatus.CANCELLED)),
            )
            .annotate(
                completion_rate=Case(
                    When(total_orders__gt=0, then=F("delivered_count") * 100.0 / F("total_orders")),
                    default=Value(0.0),
                )
            )
            .order_by("-total_revenue")
        )

    def execute(self) -> dict:
        return {"warehouses": self.get_warehouse_metrics()}


class TopProductsReport(OrderReport):
    """Rank products by sold units and revenue, excluding cancelled orders."""

    def __init__(self, *, limit: int = 10, **kwargs):
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        super().__init__(**kwargs)
        self.limit = limit

    def get_top_products(self) -> list[dict]:
        products = list(
            Product.objects.filter(organization_id=self.organization_id).values(
                "id", "sku", "name"
            )
        )
        products_by_id = {product["id"]: product for product in products}
        if not products_by_id:
            return []

        order_ids = (
            self._base_queryset()
            .exclude(status=OrderStatus.CANCELLED)
            .values("id")
        )
        aggregates = (
            OrderItem.objects.filter(
                order_id__in=order_ids,
                product_id__in=products_by_id.keys(),
            )
            .values("product_id")
            .annotate(
                units_sold=Sum("quantity"),
                revenue=Sum(F("quantity") * F("unit_price")),
                avg_price=Avg("unit_price"),
                order_count=Count("order"),
            )
            .order_by("-revenue")[: self.limit]
        )
        return [
            {
                "product__id": aggregate["product_id"],
                "product__sku": products_by_id[aggregate["product_id"]]["sku"],
                "product__name": products_by_id[aggregate["product_id"]]["name"],
                "units_sold": aggregate["units_sold"],
                "revenue": aggregate["revenue"],
                "avg_price": aggregate["avg_price"],
                "order_count": aggregate["order_count"],
            }
            for aggregate in aggregates
        ]

    def execute(self) -> dict:
        return {"top_products": self.get_top_products()}
