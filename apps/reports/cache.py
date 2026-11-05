"""Cache helpers for tenant-scoped report responses."""

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F

from apps.accounts.models import Organization

REPORT_CACHE_TIMEOUT = 300


def report_cache_key(*, organization_id, report_name, options, warehouse_ids):
    version = Organization.objects.values_list(
        "report_cache_version", flat=True
    ).get(pk=organization_id)
    identity = json.dumps(
        {
            "organization_id": organization_id,
            "report": report_name,
            "options": options,
            "warehouse_ids": warehouse_ids,
            "version": version,
        },
        sort_keys=True,
        cls=DjangoJSONEncoder,
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"reports:{digest}"


def invalidate_organization_reports(organization_id):
    """Advance the tenant's cache generation transactionally with its data."""
    updated = Organization.objects.filter(pk=organization_id).update(
        report_cache_version=F("report_cache_version") + 1
    )
    if not updated:
        raise Organization.DoesNotExist(
            f"Organization {organization_id} does not exist."
        )
