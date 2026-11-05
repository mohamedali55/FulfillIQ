"""
Custom pagination classes for FulfillIQ.

Design decisions:
─────────────────
1. We provide TWO pagination styles:
   - StandardPagination: offset-based (PageNumberPagination) for most endpoints.
     Simple, supports jumping to arbitrary pages, good for admin UIs.
   - CursorPagination: for large/real-time datasets where offset becomes
     expensive (e.g. reports with millions of rows). Uses opaque cursors
     and is O(1) regardless of page depth. Trade-off: no total count,
     no arbitrary page jumps.

2. The response includes a summary of the pagination state to make the
   API self-descriptive.
"""

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """
    Default pagination for list endpoints.

    Clients can control page size: ?page_size=25 (max 200).
    Response includes count, next, previous, and results.
    """

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "page_size": self.get_page_size(self.request),
                "current_page": self.page.number,
                "total_pages": self.page.paginator.num_pages,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )

    def get_paginated_response_schema(self, schema):
        return {
            "type": "object",
            "required": ["count", "results"],
            "properties": {
                "count": {"type": "integer"},
                "page_size": {"type": "integer"},
                "current_page": {"type": "integer"},
                "total_pages": {"type": "integer"},
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True, "format": "uri"},
                "results": schema,
            },
        }


class OrderCursorPagination(CursorPagination):
    """
    Cursor-based pagination for order lists.

    Used when:
    - The dataset is very large (100K+ orders).
    - Clients iterate sequentially (no page jumps needed).
    - Consistent ordering is required during concurrent writes.

    Orders by -created_at by default (newest first).
    """

    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200
    ordering = "-created_at"
