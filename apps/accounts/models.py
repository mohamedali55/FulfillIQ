"""
Account models — Organization and organization-scoped user profile.

Design decisions:
- Organization is the top-level tenant. Every warehouse, customer and order
  belongs to exactly one organization.
- We extend Django's built-in User rather than replacing it. UserProfile
  links a user to an organization and a role.
- Roles are simple choices for now (admin, manager, analyst, viewer).
  We'll layer DRF permission classes on top of these in milestone 3.
"""

from django.conf import settings
from django.db import models


class Organization(models.Model):
    """
    Top-level tenant. All business data is scoped to an organization.
    """

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=100, unique=True, help_text="URL-safe identifier.")
    is_active = models.BooleanField(default=True)
    report_cache_version = models.PositiveBigIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """
    Links a Django user to an organization with a role.
    One user belongs to exactly one organization.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        ANALYST = "analyst", "Analyst"
        VIEWER = "viewer", "Viewer"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="members",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "role"], name="idx_profile_org_role"),
        ]

    def __str__(self):
        return f"{self.user.username} ({self.role}) @ {self.organization}"


class UserWarehouseAccess(models.Model):
    """Grant a non-admin organization member access to one warehouse."""

    profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="warehouse_access",
    )
    warehouse = models.ForeignKey(
        "orders.Warehouse",
        on_delete=models.CASCADE,
        related_name="access_grants",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "warehouse"],
                name="uq_profile_warehouse_access",
            ),
        ]
        indexes = [
            models.Index(fields=["warehouse", "profile"], name="idx_access_wh_profile"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.profile_id and self.warehouse_id:
            if self.profile.organization_id != self.warehouse.organization_id:
                raise ValidationError(
                    {"warehouse": "Warehouse must belong to the profile's organization."}
                )

    def __str__(self):
        return f"{self.profile} → {self.warehouse}"
