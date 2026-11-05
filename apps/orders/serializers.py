"""
Order serializers — the API's data contract.

Design decisions:
─────────────────
1. We use SEPARATE serializers for list vs. detail views:
   - OrderListSerializer: lightweight, for paginated lists. No nested items.
   - OrderDetailSerializer: full order with nested items and recent history.
   This avoids the classic DRF mistake of returning 50 orders each with
   20 items and 10 history records on a list endpoint.

2. OrderItemSerializer includes product info inline (sku, name) so the
   client doesn't need a second request. This is safe because we use
   select_related('product') in the view's queryset.

3. OrderStatusTransitionSerializer is a write-only serializer for the
   transition endpoint. It validates the new_status against allowed choices
   but delegates the actual transition validation to the service layer.

4. We expose order_number (human-readable) as the lookup field, not the
   integer PK. The PK is an internal implementation detail.
"""

from rest_framework import serializers

from apps.orders.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Product,
    Warehouse,
)


# ─────────────────────────────────────────────────────────────────────────────
# Nested / read-only serializers
# ─────────────────────────────────────────────────────────────────────────────


class CustomerBriefSerializer(serializers.ModelSerializer):
    """Minimal customer info embedded in order responses."""

    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Customer
        fields = ["id", "full_name", "email"]


class WarehouseBriefSerializer(serializers.ModelSerializer):
    """Minimal warehouse info embedded in order responses."""

    class Meta:
        model = Warehouse
        fields = ["id", "code", "name", "city"]


class ProductBriefSerializer(serializers.ModelSerializer):
    """Minimal product info embedded in order item responses."""

    class Meta:
        model = Product
        fields = ["id", "sku", "name"]


class OrderItemSerializer(serializers.ModelSerializer):
    """
    Order line item with inline product info.

    line_total is computed from quantity * unit_price — no extra DB query
    because it's a Python property on the model.
    """

    product = ProductBriefSerializer(read_only=True)
    line_total = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = OrderItem
        fields = ["id", "product", "quantity", "unit_price", "line_total"]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    """Immutable status transition record."""

    class Meta:
        model = OrderStatusHistory
        fields = [
            "id",
            "previous_status",
            "new_status",
            "changed_by",
            "reason",
            "changed_at",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Order serializers — list vs. detail
# ─────────────────────────────────────────────────────────────────────────────


class OrderListSerializer(serializers.ModelSerializer):
    """
    Lightweight order representation for list/search endpoints.

    Includes inline customer and warehouse names (via select_related)
    but NO nested items or history — those are expensive on list views.
    """

    customer = CustomerBriefSerializer(read_only=True)
    warehouse = WarehouseBriefSerializer(read_only=True)
    items_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer",
            "warehouse",
            "status",
            "total_amount",
            "items_count",
            "created_at",
            "updated_at",
        ]


class OrderDetailSerializer(serializers.ModelSerializer):
    """
    Full order representation for single-order detail endpoint.

    Includes nested items (with product info) and the full status history.
    This is acceptable on a detail view because we're loading ONE order.
    """

    customer = CustomerBriefSerializer(read_only=True)
    warehouse = WarehouseBriefSerializer(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "customer",
            "warehouse",
            "status",
            "total_amount",
            "notes",
            "items",
            "status_history",
            "created_at",
            "updated_at",
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Write serializers
# ─────────────────────────────────────────────────────────────────────────────


class OrderStatusTransitionSerializer(serializers.Serializer):
    """
    Write-only serializer for the status transition endpoint.

    Validates that new_status is a recognized value. The actual transition
    validation (is this transition allowed from the current state?) is
    handled by OrderService.transition_status().
    """

    new_status = serializers.ChoiceField(choices=OrderStatus.choices)
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class OrderCreateItemSerializer(serializers.Serializer):
    """One line item in an order creation request."""

    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class OrderCreateSerializer(serializers.Serializer):
    """
    Serializer for creating a new order.

    Accepts customer_id, warehouse_id, and a list of items.
    The service layer handles order_number generation, price snapshot,
    and total calculation.
    """

    customer_id = serializers.IntegerField()
    warehouse_id = serializers.IntegerField()
    items = OrderCreateItemSerializer(many=True, min_length=1)
    notes = serializers.CharField(required=False, default="", allow_blank=True)

    def validate_customer_id(self, value):
        organization_id = self.context["request"].user.profile.organization_id
        try:
            Customer.objects.get(pk=value, organization_id=organization_id)
        except Customer.DoesNotExist:
            raise serializers.ValidationError(
                f"Customer with id {value} does not exist in your organization."
            )
        return value

    def validate_warehouse_id(self, value):
        organization_id = self.context["request"].user.profile.organization_id
        try:
            Warehouse.objects.get(pk=value, organization_id=organization_id)
        except Warehouse.DoesNotExist:
            raise serializers.ValidationError(
                f"Warehouse with id {value} does not exist in your organization."
            )
        return value

    def validate_items(self, value):
        organization_id = self.context["request"].user.profile.organization_id
        product_ids = [item["product_id"] for item in value]
        existing = set(
            Product.objects.filter(
                pk__in=product_ids,
                organization_id=organization_id,
                is_active=True,
            )
            .values_list("pk", flat=True)
        )
        missing = set(product_ids) - existing
        if missing:
            raise serializers.ValidationError(
                f"Products not found, inactive, or outside your organization: {sorted(missing)}"
            )
        return value
