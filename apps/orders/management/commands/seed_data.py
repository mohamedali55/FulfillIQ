"""
Management command to generate synthetic order data for testing and benchmarks.

Usage:
    python manage.py seed_data                  # Default: 10K orders
    python manage.py seed_data --orders 100000  # 100K orders
    python manage.py seed_data --orders 1000000 # 1M orders (for benchmarks)

Design:
- Uses Faker for realistic names, emails, cities.
- bulk_create for performance (batches of 1000).
- Deterministic seed for reproducible datasets.
- Creates a balanced distribution of statuses to simulate realistic data.
"""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from faker import Faker

from apps.accounts.models import Organization
from apps.orders.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Product,
    Warehouse,
)

fake = Faker()

# Status distribution — reflects realistic order lifecycle proportions.
STATUS_WEIGHTS = {
    OrderStatus.PENDING: 0.05,
    OrderStatus.CONFIRMED: 0.05,
    OrderStatus.PROCESSING: 0.10,
    OrderStatus.SHIPPED: 0.15,
    OrderStatus.DELIVERED: 0.50,
    OrderStatus.CANCELLED: 0.10,
    OrderStatus.RETURNED: 0.05,
}

# Status chains — the history path to reach each status.
STATUS_CHAINS: dict[str, list[str]] = {
    OrderStatus.PENDING: [OrderStatus.PENDING],
    OrderStatus.CONFIRMED: [OrderStatus.PENDING, OrderStatus.CONFIRMED],
    OrderStatus.PROCESSING: [
        OrderStatus.PENDING,
        OrderStatus.CONFIRMED,
        OrderStatus.PROCESSING,
    ],
    OrderStatus.SHIPPED: [
        OrderStatus.PENDING,
        OrderStatus.CONFIRMED,
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
    ],
    OrderStatus.DELIVERED: [
        OrderStatus.PENDING,
        OrderStatus.CONFIRMED,
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
    ],
    OrderStatus.CANCELLED: [
        OrderStatus.PENDING,
        OrderStatus.CONFIRMED,
        OrderStatus.CANCELLED,
    ],
    OrderStatus.RETURNED: [
        OrderStatus.PENDING,
        OrderStatus.CONFIRMED,
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
        OrderStatus.RETURNED,
    ],
}

WAREHOUSE_DATA = [
    ("WH-101", "East Coast Fulfillment", "New York", "US"),
    ("WH-102", "West Coast Fulfillment", "Los Angeles", "US"),
    ("WH-103", "Central Distribution", "Chicago", "US"),
    ("WH-104", "Southeast Hub", "Atlanta", "US"),
    ("WH-105", "European Hub", "London", "UK"),
]

PRODUCT_DATA = [
    ("SKU-1001", "Wireless Keyboard", "49.99"),
    ("SKU-1002", "USB-C Monitor Cable", "12.99"),
    ("SKU-1003", "Noise-Cancelling Headphones", "199.99"),
    ("SKU-1004", "Ergonomic Mouse", "34.99"),
    ("SKU-1005", "Laptop Stand", "79.99"),
    ("SKU-1006", "Webcam HD 1080p", "59.99"),
    ("SKU-1007", "Mechanical Keyboard", "129.99"),
    ("SKU-1008", "USB Hub 7-Port", "24.99"),
    ("SKU-1009", "Monitor Arm", "89.99"),
    ("SKU-1010", "Desk Lamp LED", "39.99"),
    ("SKU-1011", "Cable Management Kit", "14.99"),
    ("SKU-1012", "Wireless Charger", "29.99"),
    ("SKU-1013", "Bluetooth Speaker", "69.99"),
    ("SKU-1014", "External SSD 1TB", "109.99"),
    ("SKU-1015", "Screen Protector", "9.99"),
]


class Command(BaseCommand):
    help = "Generate synthetic order data for testing and benchmarks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--orders",
            type=int,
            default=10_000,
            help="Number of orders to generate (default: 10000).",
        )
        parser.add_argument(
            "--customers",
            type=int,
            default=500,
            help="Number of customers to generate (default: 500).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for reproducibility (default: 42).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all existing data before seeding.",
        )

    def handle(self, *args, **options):
        num_orders = options["orders"]
        num_customers = options["customers"]
        seed = options["seed"]

        random.seed(seed)
        Faker.seed(seed)

        if options["clear"]:
            self._clear_data()

        self.stdout.write(f"Seeding data: {num_orders:,} orders, {num_customers:,} customers...")

        org = self._create_org()
        warehouses = self._create_warehouses(org)
        products = self._create_products(org)
        customers = self._create_customers(org, num_customers)
        self._create_orders(org, customers, warehouses, products, num_orders)
        from apps.reports.cache import invalidate_organization_reports

        invalidate_organization_reports(org.pk)

        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def _clear_data(self):
        self.stdout.write("Clearing existing data...")
        OrderStatusHistory.objects.all().delete()
        OrderItem.objects.all().delete()
        Order.objects.all().delete()
        Customer.objects.all().delete()
        Product.objects.all().delete()
        Warehouse.objects.all().delete()
        Organization.objects.all().delete()
        self.stdout.write("  Cleared.")

    def _create_org(self) -> Organization:
        org, created = Organization.objects.get_or_create(
            slug="acme-corp",
            defaults={"name": "Acme Corporation"},
        )
        action = "Created" if created else "Using existing"
        self.stdout.write(f"  {action} organization: {org.name}")
        return org

    def _create_warehouses(self, org: Organization) -> list[Warehouse]:
        warehouses = []
        for code, name, city, country in WAREHOUSE_DATA:
            wh, _ = Warehouse.objects.get_or_create(
                code=code,
                defaults={
                    "organization": org,
                    "name": name,
                    "city": city,
                    "country": country,
                },
            )
            warehouses.append(wh)
        self.stdout.write(f"  {len(warehouses)} warehouses ready.")
        return warehouses

    def _create_products(self, org: Organization) -> list[Product]:
        products = []
        for sku, name, price in PRODUCT_DATA:
            prod, _ = Product.objects.get_or_create(
                organization=org,
                sku=sku,
                defaults={"name": name, "price": Decimal(price)},
            )
            products.append(prod)
        self.stdout.write(f"  {len(products)} products ready.")
        return products

    def _create_customers(self, org: Organization, count: int) -> list[Customer]:
        existing = Customer.objects.filter(organization=org).count()
        if existing >= count:
            self.stdout.write(f"  {existing} customers already exist, skipping.")
            return list(Customer.objects.filter(organization=org))

        to_create = count - existing
        batch = []
        for _ in range(to_create):
            batch.append(
                Customer(
                    organization=org,
                    email=fake.unique.email(),
                    first_name=fake.first_name(),
                    last_name=fake.last_name(),
                    phone=fake.phone_number()[:30],
                )
            )
        Customer.objects.bulk_create(batch, batch_size=1000)
        fake.unique.clear()
        customers = list(Customer.objects.filter(organization=org))
        self.stdout.write(f"  {len(customers)} customers ready.")
        return customers

    @transaction.atomic
    def _create_orders(
        self,
        org: Organization,
        customers: list[Customer],
        warehouses: list[Warehouse],
        products: list[Product],
        count: int,
    ):
        statuses = list(STATUS_WEIGHTS.keys())
        weights = list(STATUS_WEIGHTS.values())

        # Pre-generate all random choices for performance.
        chosen_statuses = random.choices(statuses, weights=weights, k=count)
        now = timezone.now()

        BATCH_SIZE = 1000
        total_created = 0

        for batch_start in range(0, count, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, count)
            batch_statuses = chosen_statuses[batch_start:batch_end]

            order_batch = []
            for final_status in batch_statuses:
                customer = random.choice(customers)
                warehouse = random.choice(warehouses)
                # Spread order creation over the last 90 days.
                days_ago = random.randint(0, 90)
                created_at = now - timezone.timedelta(days=days_ago)

                order = Order(
                    order_number=Order._generate_order_number(),
                    customer=customer,
                    warehouse=warehouse,
                    status=final_status,
                    notes="" if random.random() > 0.1 else fake.sentence(),
                    created_at=created_at,
                )
                order_batch.append(order)
            created_at_by_order_number = {
                order.order_number: order.created_at for order in order_batch
            }

            # Save orders to get PKs.
            Order.objects.bulk_create(order_batch, batch_size=BATCH_SIZE)

            # Reload to get assigned PKs and order_numbers.
            order_ids = [o.pk for o in order_batch]
            saved_orders = list(Order.objects.filter(pk__in=order_ids))
            for order in saved_orders:
                # auto_now_add replaces the supplied timestamp during bulk_create.
                order.created_at = created_at_by_order_number[order.order_number]

            # Create items for each order.
            item_batch = []
            for order in saved_orders:
                num_items = random.randint(1, 5)
                chosen_products = random.sample(products, min(num_items, len(products)))
                order_total = Decimal("0.00")
                for product in chosen_products:
                    qty = random.randint(1, 10)
                    price = product.price
                    item_batch.append(
                        OrderItem(
                            order=order,
                            product=product,
                            quantity=qty,
                            unit_price=price,
                        )
                    )
                    order_total += qty * price
                order.total_amount = order_total

            OrderItem.objects.bulk_create(item_batch, batch_size=BATCH_SIZE)

            # Update totals and timestamps without issuing one UPDATE per order.
            Order.objects.bulk_update(
                saved_orders,
                ["total_amount", "created_at"],
                batch_size=BATCH_SIZE,
            )

            # Create status histories.
            history_batch = []
            for i, order in enumerate(saved_orders):
                final_status = batch_statuses[i]
                chain = STATUS_CHAINS[final_status]
                base_time = order.created_at
                for step_idx, status in enumerate(chain):
                    prev = chain[step_idx - 1] if step_idx > 0 else status
                    history_batch.append(
                        OrderStatusHistory(
                            order=order,
                            previous_status=prev,
                            new_status=status,
                            changed_by="seed_data",
                            changed_at=base_time + timezone.timedelta(hours=step_idx * 2),
                        )
                    )
            history_timestamps = [history.changed_at for history in history_batch]
            OrderStatusHistory.objects.bulk_create(
                history_batch,
                batch_size=BATCH_SIZE,
            )
            for history, changed_at in zip(history_batch, history_timestamps):
                history.changed_at = changed_at
            OrderStatusHistory.objects.bulk_update(
                history_batch,
                ["changed_at"],
                batch_size=BATCH_SIZE,
            )

            total_created += len(saved_orders)
            if total_created % 5000 == 0 or total_created == count:
                self.stdout.write(f"  {total_created:,}/{count:,} orders created...")
