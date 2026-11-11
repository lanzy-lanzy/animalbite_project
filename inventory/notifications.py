"""Inventory alerts for the nurse portal.

Alerts are derived from the live inventory tables on every request instead of
being stored, so receiving stock, dispensing, administering or the passage of
time is reflected immediately and no duplicate notification records can pile
up. Nothing here writes to the database.
"""

from collections import OrderedDict
from datetime import date, timedelta
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from settings_app.models import SystemSetting

from .models import MedicineInventory, VaccineInventory

DEFAULT_EXPIRY_WARNING_DAYS = 30
EXPIRY_SETTING_KEY = 'inventory_expiry_warning_days'

BADGE_CLASSES = {
    'expired': 'bg-red-100 text-red-800',
    'out_of_stock': 'bg-red-100 text-red-800',
    'expiring': 'bg-amber-100 text-amber-800',
    'low_stock': 'bg-amber-100 text-amber-800',
}

PRIORITY = {'expired': 0, 'out_of_stock': 1, 'expiring': 2, 'low_stock': 3}
MODULE_ORDER = {'vaccine': 0, 'medicine': 1}


def get_expiry_warning_days():
    value = (
        SystemSetting.objects
        .filter(key=EXPIRY_SETTING_KEY)
        .values_list('value', flat=True)
        .first()
    )
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_EXPIRY_WARNING_DAYS
    return max(0, days)


def get_inventory_alerts(module=None):
    """Return unique active alerts, most urgent first.

    ``module`` is ``'vaccine'`` or ``'medicine'`` to scope a page; leave it
    ``None`` for the combined header bell.
    """
    warning_days = get_expiry_warning_days()
    alerts = vaccine_alerts(expiry_days=warning_days)
    alerts += medicine_alerts(expiry_days=warning_days)
    alerts.sort(key=lambda alert: (
        PRIORITY[alert['kind']],
        MODULE_ORDER[alert['module']],
        alert['expiration'] or date.max,
        alert['product'].casefold(),
        alert['batch'].casefold(),
    ))
    if module:
        alerts = [alert for alert in alerts if alert['module'] == module]
    return alerts


def _quantity_label(quantity, unit):
    suffix = '' if quantity == 1 or unit.endswith('s') else 's'
    return f'{quantity} {unit}{suffix}'


def _detail_url(module, pk, batch=None):
    name = 'vaccine_detail' if module == 'vaccine' else 'medicine_detail'
    url = reverse(f'inventory:{name}', args=[pk])
    if batch:
        url = f'{url}?{urlencode({"batch": batch})}'
    return url


def _expiry_alerts(module, batch, product, subtitle, unit,
                   today, warning_end):
    alerts = []
    if batch.quantity_available <= 0:
        return alerts
    quantity_label = _quantity_label(batch.quantity_available, unit)
    if batch.expiration_date < today:
        days_past = (today - batch.expiration_date).days
        quantity_verb = 'is' if batch.quantity_available == 1 else 'are'
        alerts.append({
            'key': f'{module}-expired-{batch.pk}',
            'kind': 'expired',
            'module': module,
            'title': 'Expired Stock',
            'product': product,
            'subtitle': subtitle,
            'batch': batch.batch_number,
            'expiration': batch.expiration_date,
            'days_left': -days_past,
            'quantity': batch.quantity_available,
            'quantity_label': quantity_label,
            'unit': unit,
            'threshold': None,
            'message': (
                f'{product} - Batch {batch.batch_number} has expired. '
                f'{quantity_label} {quantity_verb} pending pull-out.'
            ),
            'short_message': (
                f'Expired on {batch.expiration_date:%b %d, %Y} · '
                f'{quantity_label} pending pull-out.'
            ),
            'badge_class': BADGE_CLASSES['expired'],
            'url': _detail_url(module, batch.pk, batch.batch_number),
        })
    elif batch.expiration_date <= warning_end:
        days_left = (batch.expiration_date - today).days
        if days_left == 0:
            remaining = 'expires today'
        elif days_left == 1:
            remaining = '1 day left'
        else:
            remaining = f'{days_left} days left'
        alerts.append({
            'key': f'{module}-expiring-{batch.pk}',
            'kind': 'expiring',
            'module': module,
            'title': 'Expiring Soon',
            'product': product,
            'subtitle': subtitle,
            'batch': batch.batch_number,
            'expiration': batch.expiration_date,
            'days_left': days_left,
            'quantity': batch.quantity_available,
            'quantity_label': quantity_label,
            'unit': unit,
            'threshold': None,
            'message': (
                f'Expiring Soon: {product} — Batch {batch.batch_number} '
                f'will expire on {batch.expiration_date:%b %d, %Y} '
                f'({remaining}).'
            ),
            'short_message': (
                'Expires today.' if days_left == 0
                else f'Expires in {days_left} day'
                     f'{"s" if days_left != 1 else ""}.'
            ),
            'badge_class': BADGE_CLASSES['expiring'],
            'url': _detail_url(module, batch.pk, batch.batch_number),
        })
    return alerts


def _product_alerts(module, group, product, subtitle, unit, today, threshold):
    usable = sum(
        batch.quantity_available for batch in group
        if batch.expiration_date >= today
    )
    if usable <= 0:
        return [{
            'key': f'{module}-out-{group[0].pk}',
            'kind': 'out_of_stock',
            'module': module,
            'title': 'Out of Stock',
            'product': product,
            'subtitle': subtitle,
            'batch': '',
            'expiration': None,
            'days_left': None,
            'quantity': 0,
            'quantity_label': _quantity_label(0, unit),
            'unit': unit,
            'threshold': threshold,
            'message': f'Out of Stock: {product} has no remaining available stock.',
            'short_message': '0 units available.',
            'badge_class': BADGE_CLASSES['out_of_stock'],
            'url': _detail_url(module, group[0].pk),
        }]
    if usable <= threshold:
        return [{
            'key': f'{module}-low-{group[0].pk}',
            'kind': 'low_stock',
            'module': module,
            'title': 'Low Stock',
            'product': product,
            'subtitle': subtitle,
            'batch': '',
            'expiration': None,
            'days_left': None,
            'quantity': usable,
            'quantity_label': _quantity_label(usable, unit),
            'unit': unit,
            'threshold': threshold,
            'message': (
                f'Low Stock: {product} has only '
                f'{_quantity_label(usable, unit)} available '
                f'(minimum {threshold}).'
            ),
            'short_message': (
                f'Only {_quantity_label(usable, unit)} available '
                f'(minimum {threshold}).'
            ),
            'badge_class': BADGE_CLASSES['low_stock'],
            'url': _detail_url(module, group[0].pk),
        }]
    return []


def _group_by(batches, key_func):
    groups = OrderedDict()
    for batch in batches:
        groups.setdefault(key_func(batch), []).append(batch)
    return groups


def vaccine_alerts(today=None, expiry_days=None):
    today = today or timezone.localdate()
    warning_end = today + timedelta(days=(
        get_expiry_warning_days() if expiry_days is None else expiry_days))

    groups = _group_by(
        VaccineInventory.objects.all().order_by(
            'vaccine_name', 'brand', 'strength', 'dosage_form', 'unit', 'id'),
        lambda batch: (
            batch.vaccine_name.strip().casefold(),
            batch.brand.strip().casefold(),
            batch.strength.strip().casefold(),
            batch.dosage_form,
            batch.unit.strip().casefold(),
        ),
    )
    alerts = []
    for group in groups.values():
        product = group[0].vaccine_name
        subtitle = f'Vaccine · {group[0].brand}'
        if group[0].strength:
            subtitle += f' · {group[0].strength}'
        threshold = max((batch.reorder_level for batch in group), default=5)
        for batch in group:
            alerts.extend(_expiry_alerts(
                'vaccine', batch, product, subtitle, batch.unit,
                today, warning_end))
        alerts.extend(_product_alerts(
            'vaccine', group, product, subtitle, group[0].unit, today, threshold))
    return alerts


def medicine_alerts(today=None, expiry_days=None):
    today = today or timezone.localdate()
    warning_end = today + timedelta(days=(
        get_expiry_warning_days() if expiry_days is None else expiry_days))

    groups = _group_by(
        MedicineInventory.objects.all().order_by(
            'medicine_name', 'generic_name', 'strength', 'dosage_form',
            'unit', 'id'),
        lambda batch: (
            batch.medicine_name.strip().casefold(),
            batch.generic_name.strip().casefold(),
            batch.strength.strip().casefold(),
            batch.dosage_form,
            batch.unit.strip().casefold(),
        ),
    )
    alerts = []
    for group in groups.values():
        product = ' '.join(
            part for part in (group[0].medicine_name, group[0].strength) if part)
        subtitle = (
            f'{group[0].get_dosage_form_display()}'
            + (f' · {group[0].generic_name}' if group[0].generic_name else '')
        )
        threshold = max((batch.reorder_level for batch in group), default=10)
        unit = group[0].unit or 'unit'
        for batch in group:
            alerts.extend(_expiry_alerts(
                'medicine', batch, product, subtitle, unit,
                today, warning_end))
        alerts.extend(_product_alerts(
            'medicine', group, product, subtitle, unit, today, threshold))
    return alerts
