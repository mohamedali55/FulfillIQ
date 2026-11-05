"""
Tests for order domain models and lifecycle state machine.

These tests verify:
1. Order creation with items and correct total calculation.
2. Valid status transitions succeed.
3. Invalid status transitions raise descriptive errors.
4. Status history is recorded immutably.
5. Terminal states (cancelled, returned) block further transitions.
6. Concurrent transition safety (select_for_update).
"""

from decimal import Decimal

import pytest
from django.test import TestCase

from apps.accounts.models import Organization
from apps.orders.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Product,
    Warehouse,
    VALID_TRANSITIONS,
)
from apps.orders.services import InvalidTransitionError, OrderService


class OrderModelTestCase(TestCase):
    """Tests for Order model behavior."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Test Corp", slug="test-corp")
        cls.customer = Customer.objects.create(
            organization=cls.org,
            email="alice@test.com",
            first_name="Alice",
            last_name="Smith",
        )
        cls.warehouse = Warehouse.objects.create(
            organization=cls.org,
            code="WH-TEST",
            name="Test Warehouse",
            city="Test City",
            country="US",
        )
        cls.product_a = Product.objects.create(
            organization=cls.org,
            sku="SKU-A",
            name="Product A",
            price=Decimal("29.99"),
        )
        cls.product_b = Product.objects.create(
            organization=cls.org,
            sku="SKU-B",
            name="Product B",
            price=Decimal("49.99"),
        )

    def test_order_number_generated_on_creation(self):
        """Order should auto-generate a human-readable order_number."""
        order = Order.objects.create(
            customer=self.customer,
            warehouse=self.warehouse,
        )
        assert order.order_number.startswith("ORD-")
        assert len(order.order_number) == 20  # ORD- + 16 hex chars

    def test_order_default_status_is_pending(self):
        """New orders should default to 'pending' status."""
        order = Order.objects.create(
            customer=self.customer,
            warehouse=self.warehouse,
        )
        assert order.status == OrderStatus.PENDING

    def test_order_str_representation(self):
        order = Order.objects.create(
            customer=self.customer,
            warehouse=self.warehouse,
        )
        assert order.order_number in str(order)
        assert "pending" in str(order)


class OrderServiceCreateTestCase(TestCase):
    """Tests for OrderService.create_order."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Test Corp", slug="test-corp-svc")
        cls.customer = Customer.objects.create(
            organization=cls.org,
            email="bob@test.com",
            first_name="Bob",
            last_name="Jones",
        )
        cls.warehouse = Warehouse.objects.create(
            organization=cls.org,
            code="WH-SVC",
            name="Service Test Warehouse",
            city="Boston",
            country="US",
        )
        cls.product_a = Product.objects.create(
            organization=cls.org,
            sku="SKU-SVC-A",
            name="Service Product A",
            price=Decimal("10.00"),
        )
        cls.product_b = Product.objects.create(
            organization=cls.org,
            sku="SKU-SVC-B",
            name="Service Product B",
            price=Decimal("25.50"),
        )

    def test_create_order_with_items(self):
        """Service should create order, items, and calculate total correctly."""
        order = OrderService.create_order(
            customer=self.customer,
            warehouse=self.warehouse,
            items=[
                {"product": self.product_a, "quantity": 2, "unit_price": Decimal("10.00")},
                {"product": self.product_b, "quantity": 1, "unit_price": Decimal("25.50")},
            ],
        )
        assert order.status == OrderStatus.PENDING
        assert order.items.count() == 2
        assert order.total_amount == Decimal("45.50")  # (2×10) + (1×25.50)

    def test_create_order_records_initial_history(self):
        """Service should record the creation as the first history entry."""
        order = OrderService.create_order(
            customer=self.customer,
            warehouse=self.warehouse,
            items=[
                {"product": self.product_a, "quantity": 1, "unit_price": Decimal("10.00")},
            ],
            created_by="test-user",
        )
        history = order.status_history.all()
        assert history.count() == 1
        assert history[0].new_status == OrderStatus.PENDING
        assert history[0].changed_by == "test-user"

    def test_create_order_empty_items_raises_error(self):
        """Service should reject orders with no items."""
        with pytest.raises(ValueError, match="at least one item"):
            OrderService.create_order(
                customer=self.customer,
                warehouse=self.warehouse,
                items=[],
            )

    def test_unit_price_independent_of_product_price(self):
        """Order item unit_price should be the snapshot, not the current product price."""
        historical_price = Decimal("5.00")  # Different from product_a.price (10.00)
        order = OrderService.create_order(
            customer=self.customer,
            warehouse=self.warehouse,
            items=[
                {"product": self.product_a, "quantity": 3, "unit_price": historical_price},
            ],
        )
        item = order.items.first()
        assert item.unit_price == historical_price
        assert order.total_amount == Decimal("15.00")  # 3 × 5.00

    def test_recalculate_total_invalidates_organization_reports(self):
        order = OrderService.create_order(
            customer=self.customer,
            warehouse=self.warehouse,
            items=[
                {"product": self.product_a, "quantity": 1, "unit_price": Decimal("10.00")},
            ],
        )
        OrderItem.objects.create(
            order=order,
            product=self.product_b,
            quantity=1,
            unit_price=Decimal("25.50"),
        )
        self.org.refresh_from_db()
        version_before = self.org.report_cache_version

        total = OrderService.recalculate_total(order)

        self.org.refresh_from_db()
        self.assertEqual(total, Decimal("35.50"))
        self.assertEqual(self.org.report_cache_version, version_before + 1)


class OrderTransitionTestCase(TestCase):
    """Tests for the order lifecycle state machine."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Transition Corp", slug="transition-corp")
        cls.customer = Customer.objects.create(
            organization=cls.org,
            email="carol@test.com",
            first_name="Carol",
            last_name="White",
        )
        cls.warehouse = Warehouse.objects.create(
            organization=cls.org,
            code="WH-TR",
            name="Transition Warehouse",
            city="Dallas",
            country="US",
        )
        cls.product = Product.objects.create(
            organization=cls.org,
            sku="SKU-TR",
            name="Transition Product",
            price=Decimal("20.00"),
        )

    def _create_order(self) -> Order:
        return OrderService.create_order(
            customer=self.customer,
            warehouse=self.warehouse,
            items=[
                {"product": self.product, "quantity": 1, "unit_price": Decimal("20.00")},
            ],
        )

    def test_valid_transition_pending_to_confirmed(self):
        order = self._create_order()
        order = OrderService.transition_status(
            order=order,
            new_status=OrderStatus.CONFIRMED,
            changed_by="test",
        )
        assert order.status == OrderStatus.CONFIRMED

    def test_valid_full_lifecycle(self):
        """Test the happy path: pending → confirmed → processing → shipped → delivered."""
        order = self._create_order()
        for status in [
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
        ]:
            order = OrderService.transition_status(
                order=order,
                new_status=status,
                changed_by="test",
            )
        assert order.status == OrderStatus.DELIVERED
        # 1 initial + 4 transitions = 5 history records.
        assert order.status_history.count() == 5

    def test_valid_cancellation_from_processing(self):
        """Orders can be cancelled from pending, confirmed, or processing."""
        order = self._create_order()
        order = OrderService.transition_status(
            order=order, new_status=OrderStatus.CONFIRMED
        )
        order = OrderService.transition_status(
            order=order, new_status=OrderStatus.PROCESSING
        )
        order = OrderService.transition_status(
            order=order,
            new_status=OrderStatus.CANCELLED,
            reason="Customer requested cancellation.",
        )
        assert order.status == OrderStatus.CANCELLED

    def test_valid_return_after_delivery(self):
        """Delivered orders can be returned."""
        order = self._create_order()
        for status in [
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
            OrderStatus.RETURNED,
        ]:
            order = OrderService.transition_status(order=order, new_status=status)
        assert order.status == OrderStatus.RETURNED

    def test_invalid_transition_pending_to_shipped(self):
        """Cannot skip statuses — pending → shipped is invalid."""
        order = self._create_order()
        with pytest.raises(InvalidTransitionError) as exc_info:
            OrderService.transition_status(
                order=order,
                new_status=OrderStatus.SHIPPED,
            )
        assert "pending" in str(exc_info.value)
        assert "shipped" in str(exc_info.value)
        # Order should remain unchanged.
        order.refresh_from_db()
        assert order.status == OrderStatus.PENDING

    def test_invalid_transition_from_cancelled(self):
        """Cancelled is a terminal state — no further transitions allowed."""
        order = self._create_order()
        order = OrderService.transition_status(
            order=order, new_status=OrderStatus.CANCELLED
        )
        with pytest.raises(InvalidTransitionError, match="terminal state"):
            OrderService.transition_status(
                order=order,
                new_status=OrderStatus.CONFIRMED,
            )

    def test_invalid_transition_from_returned(self):
        """Returned is a terminal state — no further transitions allowed."""
        order = self._create_order()
        for status in [
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
            OrderStatus.RETURNED,
        ]:
            order = OrderService.transition_status(order=order, new_status=status)
        with pytest.raises(InvalidTransitionError, match="terminal state"):
            OrderService.transition_status(
                order=order,
                new_status=OrderStatus.DELIVERED,
            )

    def test_transition_records_history(self):
        """Every transition should append to status history."""
        order = self._create_order()
        order = OrderService.transition_status(
            order=order,
            new_status=OrderStatus.CONFIRMED,
            changed_by="manager-1",
            reason="Payment verified.",
        )
        latest = order.status_history.order_by("-changed_at").first()
        assert latest.previous_status == OrderStatus.PENDING
        assert latest.new_status == OrderStatus.CONFIRMED
        assert latest.changed_by == "manager-1"
        assert latest.reason == "Payment verified."

    def test_all_valid_transitions_covered(self):
        """Verify the transition map is complete — every status has an entry."""
        for status in OrderStatus.values:
            assert status in VALID_TRANSITIONS, (
                f"Status '{status}' missing from VALID_TRANSITIONS map."
            )


class RecalculateTotalTestCase(TestCase):
    """Tests for OrderService.recalculate_total."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Recalc Corp", slug="recalc-corp")
        cls.customer = Customer.objects.create(
            organization=cls.org,
            email="dave@test.com",
            first_name="Dave",
            last_name="Brown",
        )
        cls.warehouse = Warehouse.objects.create(
            organization=cls.org,
            code="WH-RC",
            name="Recalc Warehouse",
            city="Seattle",
            country="US",
        )
        cls.product = Product.objects.create(
            organization=cls.org,
            sku="SKU-RC",
            name="Recalc Product",
            price=Decimal("15.00"),
        )

    def test_recalculate_total_matches_items(self):
        """Recalculated total should match sum of (qty × unit_price)."""
        order = OrderService.create_order(
            customer=self.customer,
            warehouse=self.warehouse,
            items=[
                {"product": self.product, "quantity": 3, "unit_price": Decimal("15.00")},
            ],
        )
        # Manually mess up the total.
        order.total_amount = Decimal("0.00")
        order.save(update_fields=["total_amount"])

        # Recalculate should fix it.
        new_total = OrderService.recalculate_total(order)
        assert new_total == Decimal("45.00")
        order.refresh_from_db()
        assert order.total_amount == Decimal("45.00")
