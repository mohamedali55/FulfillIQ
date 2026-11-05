"""Permissions for report-export operations."""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.models import UserProfile


class ReportExportPermission(BasePermission):
    """Allow reads by all roles; only analysts and managers may queue exports."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            return False
        if not profile.organization.is_active:
            return False

        if request.method in SAFE_METHODS:
            return True
        return profile.role in {
            UserProfile.Role.ADMIN,
            UserProfile.Role.MANAGER,
            UserProfile.Role.ANALYST,
        }
