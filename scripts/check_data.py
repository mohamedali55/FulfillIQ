import os
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"
import django
django.setup()

from apps.orders.models import Order, OrderItem, OrderStatusHistory, OrderStatus
from apps.accounts.models import Organization
from apps.orders.models import Customer, Warehouse, Product

print("=== FulfillIQ Data Summary ===")
print(f"Organizations: {Organization.objects.count()}")
print(f"Customers:     {Customer.objects.count()}")
print(f"Warehouses:    {Warehouse.objects.count()}")
print(f"Products:      {Product.objects.count()}")
print(f"Orders:        {Order.objects.count()}")
print(f"Order Items:   {OrderItem.objects.count()}")
print(f"Status History:{OrderStatusHistory.objects.count()}")
print()
print("=== Order Status Distribution ===")
for status in OrderStatus.values:
    count = Order.objects.filter(status=status).count()
    pct = count / Order.objects.count() * 100
    print(f"  {status:12s}: {count:6,} ({pct:.1f}%)")
print()
print("=== Sample Orders ===")
for o in Order.objects.select_related("customer", "warehouse")[:5]:
    print(f"  {o.order_number} | {o.status:12s} | {o.warehouse.code} | {o.customer.full_name} | total={o.total_amount}")
