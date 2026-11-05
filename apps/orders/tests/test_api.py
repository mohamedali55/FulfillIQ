"""
API tests for order endpoints.

Tests cover:
1. Order list with filtering, pagination, and search
2. Order detail retrieval
3. Order creation
4. Status transitions via API
5. Authentication requirements
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Organization, UserProfile, UserWarehouseAccess
from apps.orders.models import (
    Customer,
    Order,
    OrderStatus,
    Product,
    Warehouse,
)
from apps.orders.services import OrderService


class OrderAPITestCase(TestCase):
    """Base test case with common setup for order API tests."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="API Test Corp", slug="api-test-corp")
        cls.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="testpass123"
        )
        UserProfile.objects.create(
            user=cls.user,
            organization=cls.org,
            role=UserProfile.Role.ADMIN,
        )
        cls.customer = Customer.objects.create(
            organization=cls.org,
            email="customer@test.com",
            first_name="Test",
            last_name="Customer",
        )
        cls.warehouse = Warehouse.objects.create(
            organization=cls.org,
            code="WH-API",
            name="API Test Warehouse",
            city="New York",
            country="US",
        )
        cls.product = Product.objects.create(
            organization=cls.org,
            sku="SKU-API-001",
            name="API Test Product",
            price=Decimal("99.99"),
        )

    def setUp(self):
        self.client = APIClient()
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def _create_order(self, status_val=OrderStatus.PENDING) -> Order:
        order = OrderService.create_order(
            customer=self.customer,
            warehouse=self.warehouse,
            items=[{"product": self.product, "quantity": 2, "unit_price": Decimal("99.99")}],
        )
        if status_val != OrderStatus.PENDING:
            transitions = {
                OrderStatus.CONFIRMED: [OrderStatus.CONFIRMED],
                OrderStatus.PROCESSING: [OrderStatus.CONFIRMED, OrderStatus.PROCESSING],
                OrderStatus.SHIPPED: [OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED],
                OrderStatus.DELIVERED: [
                    OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED
                ],
            }
            for s in transitions.get(status_val, []):
                order = OrderService.transition_status(order=order, new_status=s)
        return order


class OrderListAPITests(OrderAPITestCase):
    """Tests for GET /api/v1/orders/"""

    def test_list_orders_authenticated(self):
        """Authenticated users can list orders."""
        self._create_order()
        response = self.client.get("/api/v1/orders/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        self.assertEqual(response.data["count"], 1)

    def test_list_orders_unauthenticated(self):
        """Unauthenticated requests are rejected."""
        self.client.credentials()  # Remove auth
        response = self.client.get("/api/v1/orders/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_filter_by_status(self):
        """Can filter orders by status."""
        self._create_order(OrderStatus.PENDING)
        self._create_order(OrderStatus.CONFIRMED)
        response = self.client.get("/api/v1/orders/", {"status": "confirmed"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["status"], "confirmed")

    def test_filter_by_warehouse(self):
        """Can filter orders by warehouse code."""
        self._create_order()
        response = self.client.get("/api/v1/orders/", {"warehouse": "WH-API"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_search_by_order_number(self):
        """Can search orders by order_number."""
        order = self._create_order()
        response = self.client.get("/api/v1/orders/", {"search": order.order_number})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_pagination(self):
        """Response includes pagination metadata."""
        self._create_order()
        response = self.client.get("/api/v1/orders/")
        self.assertIn("count", response.data)
        self.assertIn("page_size", response.data)
        self.assertIn("current_page", response.data)

    def test_viewer_is_limited_to_granted_warehouse_and_cannot_transition(self):
        second_warehouse = Warehouse.objects.create(
            organization=self.org,
            code="WH-API-2",
            name="Second API Warehouse",
            city="Chicago",
            country="US",
        )
        assigned_order = self._create_order()
        other_order = OrderService.create_order(
            customer=self.customer,
            warehouse=second_warehouse,
            items=[{"product": self.product, "quantity": 1, "unit_price": self.product.price}],
        )
        profile = self.user.profile
        profile.role = UserProfile.Role.VIEWER
        profile.save(update_fields=["role"])
        UserWarehouseAccess.objects.create(profile=profile, warehouse=self.warehouse)

        list_response = self.client.get("/api/v1/orders/")
        detail_response = self.client.get(
            f"/api/v1/orders/{other_order.order_number}/"
        )
        transition_response = self.client.post(
            f"/api/v1/orders/{assigned_order.order_number}/transition/",
            {"new_status": "confirmed"},
            format="json",
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data["count"], 1)
        self.assertEqual(detail_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            transition_response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_order_creation_rejects_another_organizations_products(self):
        other_org = Organization.objects.create(
            name="Foreign Org", slug="foreign-org-api"
        )
        other_product = Product.objects.create(
            organization=other_org,
            sku="FOREIGN-SKU",
            name="Foreign Product",
            price=Decimal("3.00"),
        )
        response = self.client.post(
            "/api/v1/orders/",
            {
                "customer_id": self.customer.id,
                "warehouse_id": self.warehouse.id,
                "items": [{"product_id": other_product.id, "quantity": 1}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OrderDetailAPITests(OrderAPITestCase):
    """Tests for GET /api/v1/orders/{order_number}/"""

    def test_retrieve_order(self):
        """Can retrieve an order by order_number."""
        order = self._create_order()
        response = self.client.get(f"/api/v1/orders/{order.order_number}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["order_number"], order.order_number)
        self.assertIn("items", response.data)
        self.assertIn("status_history", response.data)

    def test_retrieve_nonexistent_order(self):
        """404 for nonexistent order."""
        response = self.client.get("/api/v1/orders/ORD-doesnotexist/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class OrderCreateAPITests(OrderAPITestCase):
    """Tests for POST /api/v1/orders/"""

    def test_create_order(self):
        """Can create an order via API."""
        data = {
            "customer_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "items": [{"product_id": self.product.id, "quantity": 3}],
            "notes": "Test order",
        }
        response = self.client.post("/api/v1/orders/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("order_number", response.data)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(Decimal(response.data["total_amount"]), Decimal("299.97"))

    def test_create_order_empty_items(self):
        """Cannot create order with no items."""
        data = {
            "customer_id": self.customer.id,
            "warehouse_id": self.warehouse.id,
            "items": [],
        }
        response = self.client.post("/api/v1/orders/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OrderTransitionAPITests(OrderAPITestCase):
    """Tests for POST /api/v1/orders/{order_number}/transition/"""

    def test_valid_transition(self):
        """Can transition order to valid next status."""
        order = self._create_order()
        response = self.client.post(
            f"/api/v1/orders/{order.order_number}/transition/",
            {"new_status": "confirmed", "reason": "Payment verified"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "confirmed")

    def test_invalid_transition(self):
        """Invalid transitions return 400."""
        order = self._create_order()
        response = self.client.post(
            f"/api/v1/orders/{order.order_number}/transition/",
            {"new_status": "shipped"},  # Can't skip to shipped
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)


class OrderHistoryAPITests(OrderAPITestCase):
    """Tests for GET /api/v1/orders/{order_number}/history/"""

    def test_get_order_history(self):
        """Can retrieve order status history."""
        order = self._create_order()
        OrderService.transition_status(order=order, new_status=OrderStatus.CONFIRMED)
        response = self.client.get(f"/api/v1/orders/{order.order_number}/history/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)  # Initial + transition
