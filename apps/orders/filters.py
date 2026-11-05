"""
Order filters — django-filter FilterSets for the order API.

Design decisions:
─────────────────
1. We use django-filter's FilterSet rather than hand-written query logic.
   This gives us declarative, testable, self-documenting filter definitions
   that integrate cleanly with DRF's filter backends.

2. Date range filters use IsoDateTimeFilter for ISO 8601 compatibility.
   The API accepts: ?created_after=2026-10-01&created_before=2026-11-01

3. We filter by warehouse CODE (e.g. WH-102), not by warehouse PK.
   The code is the public identifier — clients shouldn't need to know PKs.

4. total_amount range filters support queries like:
   "Show me all orders above $500" → ?total_amount_min=500
"""

from django_filters import rest_framework as filters

from apps.orders.models import Order, OrderStatus


class OrderFilter(filters.FilterSet):
    """
    FilterSet for the order list endpoint.

    Supported filters:
        ?status=delayed
        ?status=shipped&status=delivered    (multiple statuses)
        ?warehouse=WH-102                   (by warehouse code)
        ?customer_email=alice@example.com
        ?created_after=2026-10-01
        ?created_before=2026-11-01
        ?total_amount_min=100
        ?total_amount_max=1000
        ?search=ORD-a3f1                    (handled by SearchFilter, not here)
    """

    status = filters.MultipleChoiceFilter(
        choices=OrderStatus.choices,
        help_text="Filter by one or more statuses.",
    )
    warehouse = filters.CharFilter(
        field_name="warehouse__code",
        lookup_expr="exact",
        help_text="Filter by warehouse code (e.g. WH-102).",
    )
    customer_email = filters.CharFilter(
        field_name="customer__email",
        lookup_expr="iexact",
        help_text="Filter by customer email (case-insensitive).",
    )
    created_after = filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        help_text="Orders created on or after this datetime (ISO 8601).",
    )
    created_before = filters.IsoDateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        help_text="Orders created on or before this datetime (ISO 8601).",
    )
    total_amount_min = filters.NumberFilter(
        field_name="total_amount",
        lookup_expr="gte",
        help_text="Minimum order total.",
    )
    total_amount_max = filters.NumberFilter(
        field_name="total_amount",
        lookup_expr="lte",
        help_text="Maximum order total.",
    )

    class Meta:
        model = Order
        fields = []  # All filters are declared explicitly above.
