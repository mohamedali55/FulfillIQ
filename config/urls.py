"""
FulfillIQ URL Configuration.

API endpoints are versioned under /api/v1/.
"""

from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # JWT Authentication endpoints
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # API endpoints
    path("api/v1/", include("apps.orders.urls")),
    path("api/v1/reports/", include("apps.reports.urls")),
]
