from datetime import date, datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Organization, UserProfile, UserWarehouseAccess
from apps.orders.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Product,
    Warehouse,
)
from apps.orders.services import OrderService
from apps.reports.queries import (
    FulfillmentPerformanceReport,
    OrderSummaryReport,
    TopProductsReport,
    WarehouseComparisonReport,
)
from apps.reports.models import ReportExport


class ReportTestDataMixin:
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Report Org", slug="report-org")
        cls.other_org = Organization.objects.create(
            name="Other Report Org", slug="other-report-org"
        )
        cls.customer = Customer.objects.create(
            organization=cls.org,
            email="report@example.com",
            first_name="Report",
            last_name="Customer",
        )
        cls.warehouse = Warehouse.objects.create(
            organization=cls.org,
            code="RPT-1",
            name="Report Warehouse",
            city="Boston",
            country="US",
        )
        cls.other_warehouse = Warehouse.objects.create(
            organization=cls.other_org,
            code="RPT-2",
            name="Other Warehouse",
            city="Seattle",
            country="US",
        )
        cls.product = Product.objects.create(
            organization=cls.org,
            sku="RPT-SKU",
            name="Report Product",
            price=Decimal("10.00"),
        )
        cls.other_customer = Customer.objects.create(
            organization=cls.other_org,
            email="other@example.com",
            first_name="Other",
            last_name="Customer",
        )
        cls.other_product = Product.objects.create(
            organization=cls.other_org,
            sku="OTHER-SKU",
            name="Other Product",
            price=Decimal("5.00"),
        )

    def create_order(self, *, warehouse=None, customer=None, product=None, quantity=1):
        return OrderService.create_order(
            customer=customer or self.customer,
            warehouse=warehouse or self.warehouse,
            items=[
                {
                    "product": product or self.product,
                    "quantity": quantity,
                    "unit_price": (product or self.product).price,
                }
            ],
        )

    def make_delivered_order(self, *, created_at, delivered_at, other_org=False):
        if other_org:
            order = OrderService.create_order(
                customer=self.other_customer,
                warehouse=self.other_warehouse,
                items=[
                    {
                        "product": self.other_product,
                        "quantity": 1,
                        "unit_price": self.other_product.price,
                    }
                ],
            )
        else:
            order = self.create_order()

        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        order.refresh_from_db()
        for next_status in (
            OrderStatus.CONFIRMED,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
        ):
            order = OrderService.transition_status(order=order, new_status=next_status)

        timestamps = {
            OrderStatus.CONFIRMED: created_at + timedelta(hours=12),
            OrderStatus.PROCESSING: created_at + timedelta(days=1),
            OrderStatus.SHIPPED: created_at + timedelta(days=2),
            OrderStatus.DELIVERED: delivered_at,
        }
        for status_value, changed_at in timestamps.items():
            OrderStatusHistory.objects.filter(
                order=order, new_status=status_value
            ).update(changed_at=changed_at)
        return order


class ReportQueryTests(ReportTestDataMixin, TestCase):
    def test_summary_is_scoped_and_date_filtered(self):
        Order.objects.filter(
            pk=self.create_order().pk
        ).update(created_at=datetime(2026, 1, 5, tzinfo=datetime_timezone.utc))
        Order.objects.filter(
            pk=self.create_order().pk
        ).update(created_at=datetime(2026, 2, 5, tzinfo=datetime_timezone.utc))
        self.create_order(
            warehouse=self.other_warehouse,
            customer=self.other_customer,
            product=self.other_product,
        )

        result = OrderSummaryReport(
            organization_id=self.org.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        ).execute()

        self.assertEqual(result["summary"]["total_orders"], 1)
        self.assertEqual(result["summary"]["total_revenue"], Decimal("10"))
        self.assertEqual(result["by_status"], [{"status": OrderStatus.PENDING, "count": 1, "revenue": Decimal("10")}])

    def test_date_range_includes_the_entire_end_date_for_all_reports(self):
        end_of_day_order = self.create_order()
        next_day_order = self.create_order()
        Order.objects.filter(pk=end_of_day_order.pk).update(
            created_at=datetime(2026, 1, 31, 23, 59, 59, tzinfo=datetime_timezone.utc)
        )
        Order.objects.filter(pk=next_day_order.pk).update(
            created_at=datetime(2026, 2, 1, tzinfo=datetime_timezone.utc)
        )

        summary = OrderSummaryReport(
            organization_id=self.org.id,
            start_date=date(2026, 1, 31),
            end_date=date(2026, 1, 31),
        ).get_summary()
        products = TopProductsReport(
            organization_id=self.org.id,
            start_date=date(2026, 1, 31),
            end_date=date(2026, 1, 31),
        ).get_top_products()

        self.assertEqual(summary["total_orders"], 1)
        self.assertEqual(products[0]["units_sold"], 1)

    def test_performance_report_uses_actual_stage_and_delivery_times(self):
        created_at = datetime(2026, 1, 1, tzinfo=datetime_timezone.utc)
        on_time_order = self.make_delivered_order(
            created_at=created_at,
            delivered_at=created_at + timedelta(days=5),
        )
        OrderService.transition_status(
            order=on_time_order,
            new_status=OrderStatus.RETURNED,
        )
        self.make_delivered_order(
            created_at=created_at,
            delivered_at=created_at + timedelta(days=8),
        )

        report = FulfillmentPerformanceReport(
            organization_id=self.org.id,
            sla_days=7,
        )
        result = report.execute()

        self.assertEqual(result["funnel"]["delivered"], 1)
        self.assertEqual(result["funnel"]["returned"], 1)
        self.assertEqual(result["sla_compliance"]["on_time"], 1)
        self.assertEqual(result["sla_compliance"]["delayed"], 1)
        self.assertEqual(result["sla_compliance"]["on_time_rate"], 50.0)
        self.assertEqual(
            result["stage_durations"]["pending_to_confirmed"]["average_hours"],
            12.0,
        )
        self.assertEqual(
            result["stage_durations"]["shipped_to_delivered"]["sample_size"],
            2,
        )
        self.assertEqual(
            result["stage_durations"]["shipped_to_delivered"]["average_hours"],
            108.0,
        )
        self.assertTrue(Order.objects.filter(pk=on_time_order.pk).exists())

    def test_performance_report_handles_empty_results(self):
        result = FulfillmentPerformanceReport(
            organization_id=self.org.id,
            start_date=date(2030, 1, 1),
        ).execute()

        self.assertEqual(result["funnel"]["total"], 0)
        self.assertEqual(result["rates"]["completion_rate"], 0.0)
        self.assertIsNone(
            result["stage_durations"]["pending_to_confirmed"]["average_hours"]
        )
        self.assertEqual(result["sla_compliance"]["measured_deliveries"], 0)

    def test_warehouse_and_product_reports_are_tenant_scoped(self):
        self.create_order(quantity=3)
        self.create_order(
            warehouse=self.other_warehouse,
            customer=self.other_customer,
            product=self.other_product,
        )

        warehouses = WarehouseComparisonReport(
            organization_id=self.org.id
        ).get_warehouse_metrics()
        products = TopProductsReport(organization_id=self.org.id).get_top_products()

        self.assertEqual(len(warehouses), 1)
        self.assertEqual(warehouses[0]["warehouse__code"], self.warehouse.code)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["product__sku"], self.product.sku)
        self.assertEqual(products[0]["units_sold"], 3)
        self.assertEqual(products[0]["order_count"], 1)

    def test_invalid_report_options_raise(self):
        with self.assertRaises(ValueError):
            FulfillmentPerformanceReport(organization_id=self.org.id, sla_days=0)
        with self.assertRaises(ValueError):
            TopProductsReport(organization_id=self.org.id, limit=0)

    def test_warehouse_grant_rejects_cross_organization_assignment(self):
        profile = UserProfile.objects.create(
            user=User.objects.create_user(username="grant-user"),
            organization=self.org,
            role=UserProfile.Role.VIEWER,
        )
        grant = UserWarehouseAccess(profile=profile, warehouse=self.other_warehouse)

        with self.assertRaises(ValidationError):
            grant.full_clean()


class ReportAPITests(ReportTestDataMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(username="report-user", password="secret")
        UserProfile.objects.create(
            user=cls.user,
            organization=cls.org,
            role=UserProfile.Role.ADMIN,
        )
        cls.user_without_profile = User.objects.create_user(
            username="no-profile-user", password="secret"
        )

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_summary_endpoint_is_authenticated_and_tenant_scoped(self):
        self.create_order()
        self.create_order(
            warehouse=self.other_warehouse,
            customer=self.other_customer,
            product=self.other_product,
        )

        response = self.client.get("/api/v1/reports/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["total_orders"], 1)

    def test_all_report_routes_respond(self):
        paths = (
            "/api/v1/reports/fulfillment/",
            "/api/v1/reports/warehouses/",
            "/api/v1/reports/top-products/",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_date_range_validation(self):
        response = self.client.get(
            "/api/v1/reports/summary/",
            {"start_date": "2026-02-01", "end_date": "2026-01-01"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", response.data)

    def test_warehouse_filter_cannot_leak_another_organization(self):
        self.create_order(
            warehouse=self.other_warehouse,
            customer=self.other_customer,
            product=self.other_product,
        )

        response = self.client.get(
            "/api/v1/reports/summary/",
            {"warehouse_id": self.other_warehouse.id},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["total_orders"], 0)

    def test_user_without_organization_profile_is_forbidden(self):
        token = RefreshToken.for_user(self.user_without_profile).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get("/api/v1/reports/summary/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_report_data_is_restricted_to_assigned_warehouses(self):
        profile = self.user.profile
        profile.role = UserProfile.Role.VIEWER
        profile.save(update_fields=["role"])
        UserWarehouseAccess.objects.create(profile=profile, warehouse=self.warehouse)
        self.create_order()
        self.create_order(
            warehouse=self.other_warehouse,
            customer=self.other_customer,
            product=self.other_product,
        )

        response = self.client.get("/api/v1/reports/summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["summary"]["total_orders"], 1)

    def test_inactive_organization_is_forbidden(self):
        self.org.is_active = False
        self.org.save(update_fields=["is_active"])

        response = self.client.get("/api/v1/reports/summary/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_report_cache_is_invalidated_after_order_commit(self):
        first = self.client.get("/api/v1/reports/summary/")
        self.assertEqual(first.data["summary"]["total_orders"], 0)

        with self.captureOnCommitCallbacks(execute=True):
            self.create_order()

        second = self.client.get("/api/v1/reports/summary/")
        self.assertEqual(second.data["summary"]["total_orders"], 1)

    def test_report_cache_is_invalidated_after_status_transition(self):
        order = self.create_order()
        self.assertEqual(
            self.client.get("/api/v1/reports/summary/").data["by_status"],
            [{"status": OrderStatus.PENDING, "count": 1, "revenue": Decimal("10")}],
        )

        with self.captureOnCommitCallbacks(execute=True):
            OrderService.transition_status(
                order=order,
                new_status=OrderStatus.CONFIRMED,
            )

        statuses = {
            entry["status"]: entry["count"]
            for entry in self.client.get("/api/v1/reports/summary/").data["by_status"]
        }
        self.assertEqual(statuses, {OrderStatus.CONFIRMED: 1})

    def test_analyst_can_queue_and_retrieve_report_export(self):
        profile = self.user.profile
        profile.role = UserProfile.Role.ANALYST
        profile.save(update_fields=["role"])
        UserWarehouseAccess.objects.create(
            profile=profile,
            warehouse=self.warehouse,
        )
        self.create_order()

        response = self.client.post(
            "/api/v1/reports/exports/",
            {"report_type": "summary", "start_date": "2026-01-01"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["result"]["summary"]["total_orders"], 1)
        detail = self.client.get(
            f"/api/v1/reports/exports/{response.data['id']}/"
        )
        self.assertEqual(detail.status_code, status.HTTP_200_OK)

    def test_viewer_cannot_queue_report_export(self):
        profile = self.user.profile
        profile.role = UserProfile.Role.VIEWER
        profile.save(update_fields=["role"])

        response = self.client.post(
            "/api/v1/reports/exports/",
            {"report_type": "summary"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_queue_failure_is_persisted_and_returned_as_an_error(self):
        with patch(
            "apps.reports.views.generate_report_export.delay",
            side_effect=RuntimeError("broker unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/v1/reports/exports/",
                    {"report_type": "summary"},
                    format="json",
                )

        export = ReportExport.objects.get(requested_by=self.user)
        self.assertEqual(export.status, ReportExport.Status.FAILED)
        self.assertIn("RuntimeError", export.error)
