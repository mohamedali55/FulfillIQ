"""
Order views — DRF ViewSets with optimized querysets.

Design decisions:
─────────────────
1. get_queryset() is the SINGLE place where we optimize DB access.
   select_related() for FK traversals, prefetch_related() for reverse FKs,
   and annotate() for computed fields — all defined once, not scattered
   across serializers.

2. We use DIFFERENT querysets for list vs. retrieve:
   - List: select_related + annotate(items_count). NO prefetch of items/history.
   - Retrieve: select_related + prefetch_related(items, status_history).
   This prevents the classic "serialize 50 orders with all their items" mistake.

3. The transition endpoint is a custom @action, not a separate ViewSet.
   It lives on the order because it's an operation ON an order.

4. Lookup by order_number (human-readable) instead of PK.

5. Views are thin — they delegate to serializers for validation and to
   the service layer for business logic.
"""

from django.db.models import Count, Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from apps.accounts.permissions import (
    OrganizationWarehousePermission,
    accessible_warehouse_ids,
)
from apps.orders.filters import OrderFilter
from apps.orders.models import (
    Customer,
    Order,
    OrderItem,
    OrderStatusHistory,
    Product,
    Warehouse,
)
from apps.orders.pagination import StandardPagination
from apps.orders.serializers import (
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderStatusHistorySerializer,
    OrderStatusTransitionSerializer,
)
from apps.orders.services import InvalidTransitionError, OrderService


class OrderViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Order API — list, retrieve, create, and transition orders.

    GET    /api/v1/orders/                   List orders (filtered, paginated)
    GET    /api/v1/orders/{order_number}/     Retrieve order details
    POST   /api/v1/orders/                   Create a new order
    POST   /api/v1/orders/{order_number}/transition/   Transition order status
    GET    /api/v1/orders/{order_number}/history/       Full status history
    """

    pagination_class = StandardPagination
    permission_classes = [OrganizationWarehousePermission]
    filterset_class = OrderFilter
    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]
    # SearchFilter: searches order_number and customer email.
    search_fields = ["order_number", "customer__email", "customer__last_name"]
    # OrderingFilter: allowed sort fields.
    ordering_fields = ["created_at", "updated_at", "total_amount", "status"]
    ordering = ["-created_at"]  # Default ordering.

    # Use order_number as the URL lookup instead of PK.
    lookup_field = "order_number"

    def get_queryset(self):
        """
        Return the optimized queryset for the current action.

        LIST:     select_related + annotate(items_count)
                  → 1 query total, no N+1.
        RETRIEVE: select_related + prefetch_related(items__product, status_history)
                  → 3 queries total (order + items + history), no N+1.
        """
        profile = self.request.user.profile
        base = Order.objects.filter(
            warehouse__organization_id=profile.organization_id
        ).select_related("customer", "warehouse")
        warehouse_ids = accessible_warehouse_ids(profile)
        if warehouse_ids is not None:
            base = base.filter(warehouse_id__in=warehouse_ids)

        if self.action == "list":
            return base.annotate(items_count=Count("items"))
        elif self.action in ("retrieve", "transition", "history"):
            return base.prefetch_related(
                Prefetch(
                    "items",
                    queryset=OrderItem.objects.select_related("product").order_by("id"),
                ),
                Prefetch(
                    "status_history",
                    queryset=OrderStatusHistory.objects.order_by("-changed_at"),
                ),
            )
        return base

    def get_serializer_class(self):
        """Use different serializers for list vs. detail."""
        if self.action == "list":
            return OrderListSerializer
        elif self.action == "create":
            return OrderCreateSerializer
        elif self.action == "transition":
            return OrderStatusTransitionSerializer
        return OrderDetailSerializer

    def create(self, request, *args, **kwargs):
        """
        Create a new order.

        POST /api/v1/orders/
        {
            "customer_id": 1,
            "warehouse_id": 1,
            "items": [
                {"product_id": 1, "quantity": 2},
                {"product_id": 3, "quantity": 1}
            ],
            "notes": "Rush order"
        }
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        customer = Customer.objects.get(pk=data["customer_id"])
        warehouse = Warehouse.objects.get(pk=data["warehouse_id"])

        # Look up products and snapshot their current prices.
        items = []
        for item_data in data["items"]:
            product = Product.objects.get(pk=item_data["product_id"])
            items.append(
                {
                    "product": product,
                    "quantity": item_data["quantity"],
                    "unit_price": product.price,  # Price snapshot at order time.
                }
            )

        changed_by = (
            request.user.username
            if request.user.is_authenticated
            else "anonymous"
        )

        order = OrderService.create_order(
            customer=customer,
            warehouse=warehouse,
            items=items,
            notes=data.get("notes", ""),
            created_by=changed_by,
        )

        # Return the full detail serializer for the created order.
        detail_serializer = OrderDetailSerializer(order)
        return Response(detail_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="transition")
    def transition(self, request, order_number=None):
        """
        Transition an order to a new status.

        POST /api/v1/orders/{order_number}/transition/
        {
            "new_status": "confirmed",
            "reason": "Payment verified"
        }
        """
        order = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        changed_by = (
            request.user.username
            if request.user.is_authenticated
            else "anonymous"
        )

        try:
            order = OrderService.transition_status(
                order=order,
                new_status=serializer.validated_data["new_status"],
                changed_by=changed_by,
                reason=serializer.validated_data.get("reason", ""),
            )
        except InvalidTransitionError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        detail_serializer = OrderDetailSerializer(order)
        return Response(detail_serializer.data)

    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, order_number=None):
        """
        Retrieve the full status history for an order.

        GET /api/v1/orders/{order_number}/history/
        """
        order = self.get_object()
        history = order.status_history.all()
        serializer = OrderStatusHistorySerializer(history, many=True)
        return Response(serializer.data)
