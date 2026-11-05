"""
Order business logic — the service layer.

Design decisions:
─────────────────
- Business rules live here, not in models or views.
- Models handle data shape and constraints.
- Views handle HTTP concerns.
- Services handle business logic: validation, state transitions, calculations.

This separation means:
  1. Business rules are testable without HTTP.
  2. The same logic works whether called from an API view, a management
     command, or a Celery task.
  3. We can unit-test state transitions without touching the database
     (by mocking the model).
"""

from decimal import Decimal

from django.db import transaction

from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    VALID_TRANSITIONS,
)


class InvalidTransitionError(Exception):
    """Raised when an order status transition is not allowed."""

    def __init__(self, order_number: str, current: str, requested: str):
        self.order_number = order_number
        self.current = current
        self.requested = requested
        allowed = VALID_TRANSITIONS.get(current, set())
        super().__init__(
            f"Cannot transition order {order_number} from '{current}' to '{requested}'. "
            f"Allowed transitions from '{current}': {sorted(allowed) or 'none (terminal state)'}."
        )


class OrderService:
    """
    Encapsulates order business operations.

    All methods that modify data use @transaction.atomic to ensure
    consistency — either everything commits or nothing does.
    """

    @staticmethod
    @transaction.atomic
    def create_order(
        *,
        customer,
        warehouse,
        items: list[dict],
        notes: str = "",
        created_by: str = "system",
    ) -> Order:
        """
        Create an order with line items in a single transaction.

        Args:
            customer: Customer instance.
            warehouse: Warehouse instance.
            items: List of dicts with keys: product, quantity, unit_price.
            notes: Optional order notes.
            created_by: Username or identifier for audit trail.

        Returns:
            The created Order with items and initial history entry.

        Raises:
            ValueError: If items list is empty.
        """
        if not items:
            raise ValueError("An order must have at least one item.")
        if customer.organization_id != warehouse.organization_id:
            raise ValueError("Customer and warehouse must belong to the same organization.")
        for item in items:
            if item["product"].organization_id != warehouse.organization_id:
                raise ValueError("Products must belong to the order's organization.")
            if item["quantity"] < 1:
                raise ValueError("Item quantity must be at least one.")
            if Decimal(str(item["unit_price"])) < 0:
                raise ValueError("Item unit price cannot be negative.")

        order = Order.objects.create(
            customer=customer,
            warehouse=warehouse,
            status=OrderStatus.PENDING,
            notes=notes,
        )

        # Bulk-create order items for efficiency.
        order_items = [
            OrderItem(
                order=order,
                product=item["product"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
            )
            for item in items
        ]
        OrderItem.objects.bulk_create(order_items)

        # Calculate and set the denormalized total.
        order.total_amount = sum(
            Decimal(str(item["quantity"])) * Decimal(str(item["unit_price"]))
            for item in items
        )
        order.save(update_fields=["total_amount"])

        # Record the initial status in the audit trail.
        OrderStatusHistory.objects.create(
            order=order,
            previous_status=OrderStatus.PENDING,
            new_status=OrderStatus.PENDING,
            changed_by=created_by,
            reason="Order created.",
        )

        OrderService._invalidate_report_cache(
            order.warehouse.organization_id
        )
        return order

    @staticmethod
    @transaction.atomic
    def transition_status(
        *,
        order: Order,
        new_status: str,
        changed_by: str = "system",
        reason: str = "",
    ) -> Order:
        """
        Transition an order to a new status.

        Validates the transition against the state machine, updates the
        order, and appends an immutable history record — all atomically.

        Args:
            order: The Order instance to transition.
            new_status: The target status (must be a valid OrderStatus value).
            changed_by: Username or identifier for audit trail.
            reason: Optional reason for the transition.

        Returns:
            The updated Order.

        Raises:
            InvalidTransitionError: If the transition is not allowed.

        Uses select_for_update() to prevent concurrent transitions
        on the same order from causing inconsistent state.
        """
        # Re-fetch with a row-level lock to prevent concurrent transitions.
        order = Order.objects.select_for_update().get(pk=order.pk)
        current_status = order.status

        # Validate the transition.
        allowed = VALID_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise InvalidTransitionError(order.order_number, current_status, new_status)

        # Apply the transition.
        order.status = new_status
        order.save(update_fields=["status", "updated_at"])

        # Append to the immutable audit trail.
        OrderStatusHistory.objects.create(
            order=order,
            previous_status=current_status,
            new_status=new_status,
            changed_by=changed_by,
            reason=reason,
        )

        OrderService._invalidate_report_cache(
            order.warehouse.organization_id
        )
        return order

    @staticmethod
    def _invalidate_report_cache(organization_id: int) -> None:
        from apps.reports.cache import invalidate_organization_reports

        invalidate_organization_reports(organization_id)

    @staticmethod
    @transaction.atomic
    def recalculate_total(order: Order) -> Decimal:
        """
        Recalculate the denormalized total_amount from order items.

        Uses database-level aggregation — no Python loop over items.
        Returns the new total and saves it to the order.
        """
        from django.db.models import F, Sum

        result = order.items.aggregate(
            total=Sum(F("quantity") * F("unit_price"))
        )
        total = result["total"] or Decimal("0.00")
        order.total_amount = total
        order.save(update_fields=["total_amount"])
        OrderService._invalidate_report_cache(
            order.warehouse.organization_id
        )
        return total
