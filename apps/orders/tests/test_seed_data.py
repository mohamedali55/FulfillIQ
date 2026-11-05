from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import Organization
from apps.orders.models import Order, OrderStatusHistory


class SeedDataCommandTestCase(TestCase):
    def test_seeded_order_timestamps_are_spread_and_match_history(self):
        call_command(
            "seed_data",
            orders=50,
            customers=20,
            seed=4205,
            stdout=StringIO(),
        )

        organization = Organization.objects.get(slug="acme-corp")
        orders = Order.objects.filter(warehouse__organization=organization)
        timestamps = list(orders.values_list("created_at", flat=True))
        self.assertGreater((max(timestamps) - min(timestamps)).days, 7)

        order = orders.first()
        first_history = OrderStatusHistory.objects.filter(order=order).order_by(
            "changed_at"
        ).first()
        self.assertEqual(first_history.changed_at, order.created_at)
