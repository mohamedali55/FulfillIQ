from django.contrib import admin

from apps.accounts.models import Organization, UserProfile, UserWarehouseAccess


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "created_at")
    list_filter = ("role", "organization")
    search_fields = ("user__username", "user__email")


@admin.register(UserWarehouseAccess)
class UserWarehouseAccessAdmin(admin.ModelAdmin):
    list_display = ("profile", "warehouse", "created_at")
    list_filter = ("warehouse__organization", "profile__role")
    search_fields = ("profile__user__username", "warehouse__code", "warehouse__name")
