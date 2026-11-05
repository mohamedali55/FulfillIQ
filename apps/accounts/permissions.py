"""Shared organization and warehouse authorization helpers."""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.models import UserProfile


READ_ROLES = {
    UserProfile.Role.ADMIN,
    UserProfile.Role.MANAGER,
    UserProfile.Role.ANALYST,
    UserProfile.Role.VIEWER,
}
WRITE_ROLES = {UserProfile.Role.ADMIN, UserProfile.Role.MANAGER}


def accessible_warehouse_ids(profile):
    """Return None for full organization access, otherwise granted warehouse IDs."""
    if profile.role in {UserProfile.Role.ADMIN, UserProfile.Role.MANAGER}:
        return None
    return list(
        profile.warehouse_access.filter(
            warehouse__organization_id=profile.organization_id
        ).values_list("warehouse_id", flat=True)
    )


class OrganizationWarehousePermission(BasePermission):
    """Require an active organization profile and enforce role-based writes."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            return False

        if not profile.organization.is_active:
            return False

        action = getattr(view, "action", None)
        if request.method not in SAFE_METHODS or action in {
            "create",
            "transition",
            "update",
            "partial_update",
            "destroy",
        }:
            return profile.role in WRITE_ROLES
        return profile.role in READ_ROLES

    def has_object_permission(self, request, view, obj):
        try:
            profile = request.user.profile
        except UserProfile.DoesNotExist:
            return False

        warehouse = getattr(obj, "warehouse", None)
        if warehouse is None or warehouse.organization_id != profile.organization_id:
            return False
        allowed = accessible_warehouse_ids(profile)
        return allowed is None or warehouse.pk in allowed
