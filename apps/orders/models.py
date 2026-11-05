"""
Order domain models — the core of FulfillIQ.

Design decisions:
─────────────────
1. Order stores the current status; OrderStatusHistory stores every transition.
   This gives us O(1) lookups for current status and a full audit trail.

2. OrderItem captures unit_price at the time of order creation, so reports
   stay accurate even when Product.price changes later. This is a standard
   enterprise pattern — the order is a snapshot of the price at purchase time.

3. Status transitions are enforced in the service layer (services.py), not in
   the model's save(). This keeps the model clean and lets us raise descriptive
   errors with the invalid transition details.

4. Indexes are chosen to support the queries we'll build in milestones 3–4:
   - Orders filtered by status + warehouse (the most common report query)
   - Orders filtered by customer
   - Orders filtered by created_at ranges
   - OrderItems looked up by order (already covered by the FK index)

5. We use a human-readable order_number (e.g. ORD-00010482) alongside the
   auto-increment PK. The PK is for joins; the order_number is for display
   and API lookups.
"""

import uuid

from django.core.validators import MinValueValidator
from django.db import models

from apps.accounts.models import Organization


class Customer(models.Model):
    """A customer who places orders within an organization."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="customers",
    )
    email = models.EmailField()
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "email"],
                name="uq_customer_org_email",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "last_name"], name="idx_customer_org_name"),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Warehouse(models.Model):
    """A fulfillment warehouse belonging to an organization."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="warehouses",
    )
    code = models.CharField(
        max_length=20,
        unique=True,
        help_text="Short identifier, e.g. WH-102",
    )
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "warehouses"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Product(models.Model):
    """A product that can appear in order line items."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="products",
    )
    sku = models.CharField(max_length=50, help_text="Stock Keeping Unit")
    name = models.CharField(max_length=255)
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Current catalog price. OrderItem.unit_price captures the price at order time.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sku"],
                name="uq_product_org_sku",
            ),
        ]

    def __str__(self):
        return f"{self.sku} — {self.name}"


class OrderStatus(models.TextChoices):
    """
    All valid order statuses and the transitions between them.

    State machine:
        pending → confirmed → processing → shipped → delivered
                                  ↓                      ↓
                              cancelled              returned

    Valid transitions are defined in VALID_TRANSITIONS below.
    """

    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    PROCESSING = "processing", "Processing"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"
    RETURNED = "returned", "Returned"


# Explicit transition map — every allowed (from → to) pair.
# If a transition isn't here, it's invalid.
VALID_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: {OrderStatus.RETURNED},
    OrderStatus.CANCELLED: set(),  # terminal state
    OrderStatus.RETURNED: set(),  # terminal state
}


class Order(models.Model):
    """
    A customer order fulfilled from a specific warehouse.

    The order_number is a human-readable identifier generated on creation.
    The total_amount is denormalized for query performance — it's the sum
    of (quantity × unit_price) across all OrderItems. It's set by the
    service layer, not by the model.
    """

    order_number = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        help_text="Human-readable order ID, e.g. ORD-00010482",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
        db_index=True,
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Denormalized total = sum(item.quantity * item.unit_price)",
    )
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The primary report query: filter by status + warehouse + date range.
            models.Index(
                fields=["status", "warehouse", "created_at"],
                name="idx_order_status_wh_created",
            ),
            models.Index(
                fields=["warehouse", "created_at"],
                name="idx_order_wh_created",
            ),
            # Customer order lookup.
            models.Index(
                fields=["customer", "created_at"],
                name="idx_order_customer_created",
            ),
        ]

    def __str__(self):
        return f"{self.order_number} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_order_number() -> str:
        """
        Generate a unique order number.
        Uses a 64-bit UUID4 prefix to keep collision probability negligible
        for large datasets while remaining within the database field limit.
        Format: ORD-<16 hex chars>, e.g. ORD-a3f1b20e4c91d256
        """
        return f"ORD-{uuid.uuid4().hex[:16]}"


class OrderItem(models.Model):
    """
    A line item in an order.

    unit_price is captured at order creation time — it's the price the
    customer actually paid, independent of future product price changes.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_items",
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Price per unit at the time of order creation.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "product"],
                name="uq_orderitem_order_product",
            ),
        ]

    def __str__(self):
        return f"{self.product.sku} ×{self.quantity} @ {self.unit_price}"

    @property
    def line_total(self):
        return self.quantity * self.unit_price


class OrderStatusHistory(models.Model):
    """
    Immutable audit log of every order status transition.

    Each row records who changed the status, when, and the before/after states.
    This table is append-only — rows are never updated or deleted.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    previous_status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
    )
    new_status = models.CharField(
        max_length=20,
        choices=OrderStatus.choices,
    )
    changed_by = models.CharField(
        max_length=150,
        blank=True,
        default="system",
        help_text="Username or system identifier that triggered the change.",
    )
    reason = models.TextField(
        blank=True,
        default="",
        help_text="Optional reason for the status change.",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]
        verbose_name_plural = "order status histories"
        indexes = [
            models.Index(
                fields=["order", "changed_at"],
                name="idx_history_order_changed",
            ),
        ]

    def __str__(self):
        return f"{self.order.order_number}: {self.previous_status} → {self.new_status}"
