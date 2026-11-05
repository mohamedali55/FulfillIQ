from django.contrib import admin

from apps.orders.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatusHistory,
    Product,
    Warehouse,
)
from apps.orders.services import OrderService


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("line_total",)


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ("previous_status", "new_status", "changed_by", "reason", "changed_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False  # History is immutable — append via service layer only.


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("first_name", "last_name", "email")


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "city", "country", "organization", "is_active")
    list_filter = ("organization", "is_active", "country")
    search_fields = ("code", "name", "city")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "price", "organization", "is_active")
    list_filter = ("organization", "is_active")
    search_fields = ("sku", "name")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "customer", "warehouse", "status", "total_amount", "created_at")
    list_filter = ("status", "warehouse", "created_at")
    search_fields = ("order_number",)
    readonly_fields = ("order_number", "total_amount", "status")
    inlines = [OrderItemInline, OrderStatusHistoryInline]
    date_hierarchy = "created_at"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        OrderService._invalidate_report_cache(obj.warehouse.organization_id)

    def save_formset(self, request, form, formset, change):
        super().save_formset(request, form, formset, change)
        if formset.model is OrderItem:
            OrderService.recalculate_total(formset.instance)
