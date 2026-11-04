from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, F, Sum
from django.views.decorators.http import require_POST
from functools import wraps
from datetime import date
from .models import (
    VaccineInventory, SupplyInventory, VaccineStockMovement,
    MedicineInventory, MedicineStockMovement,
)
from .forms import (
    VaccineInventoryForm, VaccineAddStockForm, SupplyInventoryForm,
    VaccineStockMovementForm, MedicineInventoryForm, MedicineAddStockForm,
)
from audit.models import AuditLog
from doctor.models import Prescription

# Medicine stock is managed by clinic staff; dispensing needs an authorized
# hand (nurse or admin) so the transaction record reflects who released it.
STAFF_ROLES = {'admin', 'nurse', 'health_worker', 'doctor', 'encoder'}
DISPENSER_ROLES = {'admin', 'nurse', 'health_worker'}


def staff_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if getattr(request.user, 'role', '') not in STAFF_ROLES:
            messages.error(request, 'Not authorized to view inventory.')
            return redirect('dashboard:index')
        return view_func(request, *args, **kwargs)
    return _wrapped


def can_dispense(user):
    return getattr(user, 'role', '') in DISPENSER_ROLES

def _nurse_vaccine_groups(query=''):
    """Collapse batch rows into one row per vaccine product for the nurse list.

    Product identity is the vaccine name plus brand; each batch row keeps its own
    quantities, deliveries and expiry. Search still matches any batch's fields but
    only the matching products are grouped and shown.
    """
    batches = list(
        VaccineInventory.objects.all().order_by(
            'vaccine_name', 'brand', 'expiration_date', 'id')
    )
    needle = query.strip().lower()
    matching_keys = set()
    if needle:
        for batch in batches:
            if (needle in batch.vaccine_name.lower()
                    or needle in batch.brand.lower()
                    or needle in batch.batch_number.lower()):
                matching_keys.add((
                    batch.vaccine_name.strip().casefold(),
                    batch.brand.strip().casefold(),
                ))

    groups = {}
    for batch in batches:
        key = (
            batch.vaccine_name.strip().casefold(),
            batch.brand.strip().casefold(),
        )
        if needle and key not in matching_keys:
            continue
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                'pk': batch.pk,
                'vaccine_name': batch.vaccine_name,
                'brand': batch.brand,
                'batch_count': 0,
                'quantity_used': 0,
                'quantity_available': 0,
                'batch_expiries': [],
            }
        group['batch_count'] += 1
        group['quantity_used'] += batch.quantity_used
        if not batch.is_expired():
            group['quantity_available'] += batch.quantity_available
        group['batch_expiries'].append((
            batch.batch_number, batch.expiration_date))

    today = date.today()
    for group in groups.values():
        group['batch_label'] = (
            f"{group['batch_count']} batch"
            + ('' if group['batch_count'] == 1 else 'es')
        )
        upcoming = sorted({
            expiry for _, expiry in group['batch_expiries'] if expiry >= today
        })
        if not upcoming:
            group['expiry_label'] = 'Expired'
        elif len(upcoming) == 1:
            group['expiry_label'] = upcoming[0].strftime('%b %d, %Y')
        else:
            group['expiry_label'] = 'Multiple Expiries'
        group['expiry_title'] = ' • '.join(
            (f'{number}: {expiry.strftime("%b %d, %Y")}'
             if number.lower().startswith('batch')
             else f'Batch {number}: {expiry.strftime("%b %d, %Y")}')
            for number, expiry in group['batch_expiries']
        )
        if not upcoming:
            group['status_label'] = 'Expired'
            group['status_class'] = 'bg-red-100 text-red-800'
        elif group['quantity_available'] <= 5:
            group['status_label'] = 'Low Stock'
            group['status_class'] = 'bg-yellow-100 text-yellow-800'
        else:
            group['status_label'] = 'Available'
            group['status_class'] = 'bg-green-100 text-green-800'
    return list(groups.values())


@login_required
def vaccine_list(request):
    query = request.GET.get('q', '')
    if getattr(request.user, 'role', '') == 'nurse':
        paginator = Paginator(_nurse_vaccine_groups(query), 20)
        page = paginator.get_page(request.GET.get('page'))
        return render(request, 'inventory/vaccine_list.html',
                      {'items': page, 'query': query})
    items = VaccineInventory.objects.all()
    if query:
        items = items.filter(Q(vaccine_name__icontains=query) | Q(brand__icontains=query) | Q(batch_number__icontains=query))
    paginator = Paginator(items.order_by('-date_received'), 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'inventory/vaccine_list.html', {'items': page, 'query': query})

@login_required
def vaccine_create(request):
    if request.method == 'POST':
        form = VaccineInventoryForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.quantity_available = item.quantity_received
            item.created_by = request.user
            item.save()
            VaccineStockMovement.objects.create(
                vaccine=item, movement_type='stock_in', quantity=item.quantity_received,
                supplier=item.source_supplier, date_received=item.date_received,
                batch_number=item.batch_number, expiration_date=item.expiration_date,
                moved_by=request.user, notes='Initial stock in'
            )
            AuditLog.objects.create(user=request.user, action='STOCK_IN', description=f'Added vaccine {item.vaccine_name} batch {item.batch_number}')
            messages.success(request, 'Vaccine inventory added.')
            return redirect('inventory:vaccine_list')
    else:
        form = VaccineInventoryForm()
    return render(request, 'inventory/vaccine_form.html', {'form': form, 'title': 'Add Vaccine Stock'})


@login_required
def vaccine_add_stock(request, pk):
    if getattr(request.user, 'role', '') not in ('nurse', 'admin'):
        messages.error(request, 'Not authorized to add vaccine stock.')
        return redirect('dashboard:index')
    vaccine = get_object_or_404(VaccineInventory, pk=pk)
    if request.method == 'POST':
        form = VaccineAddStockForm(request.POST, vaccine=vaccine)
        if form.is_valid():
            batch, created, duplicate = form.save(request.user)
            if duplicate:
                messages.info(
                    request,
                    'This stock delivery was already recorded. '
                    'Inventory was not changed.',
                )
                return redirect('inventory:vaccine_list')
            quantity = form.cleaned_data['quantity_received']
            supplier = form.cleaned_data['source_supplier']
            AuditLog.objects.create(
                user=request.user, action='STOCK_IN',
                description=(
                    f'Recorded delivery of {quantity} for {batch.vaccine_name} '
                    f'batch {batch.batch_number} ({supplier})'
                ),
            )
            messages.success(request, 'Stock added successfully.')
            return redirect('inventory:vaccine_list')
    else:
        form = VaccineAddStockForm(vaccine=vaccine)
    return render(request, 'inventory/vaccine_add_stock.html', {
        'form': form,
        'vaccine': vaccine,
    })

@login_required
def vaccine_detail(request, pk):
    item = get_object_or_404(VaccineInventory, pk=pk)
    if getattr(request.user, 'role', '') != 'nurse':
        movements = item.movements.select_related('moved_by').order_by('-created_at')
        return render(request, 'inventory/vaccine_detail.html', {'item': item, 'movements': movements})

    # The nurse view is product-level: every batch of the same vaccine name and
    # brand contributes to the summary, history and administration records.
    product_batches = VaccineInventory.objects.filter(
        vaccine_name__iexact=item.vaccine_name,
        brand__iexact=item.brand,
    )
    totals = product_batches.aggregate(
        received=Sum('quantity_received'),
        used=Sum('quantity_used'),
    )
    live_batches = [batch for batch in product_batches if not batch.is_expired()]
    live_available = sum(batch.quantity_available for batch in live_batches)
    totals['available'] = live_available
    movements = (
        VaccineStockMovement.objects
        .filter(vaccine__in=product_batches)
        .select_related('moved_by', 'vaccine')
        .order_by('-created_at')
    )

    # A dose records the exact batch it was administered from and deducts one
    # vial on confirmation, so matching on the batch ties real administrations
    # back to this product without adding new database relationships.
    from vaccination.models import VaccineDose
    batch_numbers = [
        number for number in product_batches.values_list('batch_number', flat=True)
        if number
    ]
    dose_filter = Q()
    for batch_number in batch_numbers:
        dose_filter |= Q(batch_number__iexact=batch_number)
    if dose_filter:
        dispensing_records = (
            VaccineDose.objects
            .filter(dose_filter)
            .select_related(
                'schedule__bite_case__patient',
                'administered_by',
            )
            .order_by('-actual_date', '-actual_time', '-id')
        )
    else:
        dispensing_records = VaccineDose.objects.none()

    if not live_batches:
        stock_status = 'expired'
    elif live_available <= 5:
        stock_status = 'low'
    else:
        stock_status = 'available'

    return render(request, 'inventory/vaccine_detail_nurse.html', {
        'item': item,
        'totals': totals,
        'movements': movements,
        'dispensing_records': dispensing_records,
        'stock_status': stock_status,
    })

def _nurse_medicine_groups(query=''):
    """Collapse medicine batch rows into one row per medicine product.

    Product identity covers name, generic/brand name, strength, dosage form and
    unit so clinically distinct medicines never merge. Each batch keeps its own
    quantities, deliveries and transactions; expired stock is excluded from the
    usable available total.
    """
    batches = list(
        MedicineInventory.objects.all().order_by(
            'medicine_name', 'generic_name', 'strength', 'dosage_form',
            'unit', 'expiration_date', 'id')
    )
    needle = query.strip().casefold()
    matching_keys = set()
    if needle:
        for batch in batches:
            haystacks = (
                batch.medicine_name,
                batch.generic_name,
                batch.strength,
                batch.unit,
                batch.batch_number,
                batch.get_dosage_form_display(),
            )
            if any(needle in value.casefold() for value in haystacks):
                matching_keys.add(_medicine_product_key(batch))

    groups = {}
    for batch in batches:
        key = _medicine_product_key(batch)
        if needle and key not in matching_keys:
            continue
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                'pk': batch.pk,
                'medicine_name': batch.medicine_name,
                'generic_name': batch.generic_name,
                'strength': batch.strength,
                'dosage_form_display': batch.get_dosage_form_display(),
                'unit': batch.unit,
                'batch_count': 0,
                'quantity_available': 0,
                'reorder_level': 0,
                'batch_expiries': [],
            }
        group['batch_count'] += 1
        group['reorder_level'] = max(
            group['reorder_level'], batch.reorder_level)
        if not batch.is_expired():
            group['quantity_available'] += batch.quantity_available
        group['batch_expiries'].append((
            batch.batch_number, batch.expiration_date))

    today = date.today()
    for group in groups.values():
        group['batch_label'] = (
            f"{group['batch_count']} Batch"
            + ('' if group['batch_count'] == 1 else 'es')
        )
        upcoming = sorted({
            expiry for _, expiry in group['batch_expiries'] if expiry >= today
        })
        if not upcoming:
            group['expiry_label'] = 'Expired'
        elif len(upcoming) == 1:
            group['expiry_label'] = upcoming[0].strftime('%b %d, %Y')
        else:
            group['expiry_label'] = 'Multiple Expiries'
        group['expiry_title'] = ' • '.join(
            (f'{number}: {expiry.strftime("%b %d, %Y")}'
             if number.lower().startswith('batch')
             else f'Batch {number}: {expiry.strftime("%b %d, %Y")}')
            for number, expiry in group['batch_expiries']
        )
        if not upcoming:
            group['status_label'] = 'Expired'
            group['status_class'] = 'bg-red-100 text-red-800'
        elif group['quantity_available'] <= 0:
            group['status_label'] = 'Out of Stock'
            group['status_class'] = 'bg-red-100 text-red-800'
        elif group['quantity_available'] <= group['reorder_level']:
            group['status_label'] = 'Low Stock'
            group['status_class'] = 'bg-yellow-100 text-yellow-800'
        else:
            group['status_label'] = 'Available'
            group['status_class'] = 'bg-green-100 text-green-800'
    return list(groups.values())


def _medicine_product_key(batch):
    return (
        batch.medicine_name.strip().casefold(),
        batch.generic_name.strip().casefold(),
        batch.strength.strip().casefold(),
        batch.dosage_form,
        batch.unit.strip().casefold(),
    )


@login_required
@staff_required
def medicine_list(request):
    query = request.GET.get('q', '')
    state = request.GET.get('status', '')
    pending_count = Prescription.objects.filter(status='pending').count()
    if getattr(request.user, 'role', '') == 'nurse':
        paginator = Paginator(_nurse_medicine_groups(query), 20)
        page = paginator.get_page(request.GET.get('page'))
        return render(request, 'inventory/medicine_list.html', {
            'items': page,
            'query': query,
            'state': state,
            'pending_count': pending_count,
        })
    items = MedicineInventory.objects.all()
    if query:
        items = items.filter(
            Q(medicine_name__icontains=query) |
            Q(generic_name__icontains=query) |
            Q(strength__icontains=query) |
            Q(batch_number__icontains=query)
        )
    if state == 'low':
        items = items.filter(quantity_available__gt=0,
                             quantity_available__lte=F('reorder_level'))
    elif state == 'out':
        items = items.filter(quantity_available__lte=0)
    elif state == 'expired':
        items = items.filter(expiration_date__lt=date.today())
    paginator = Paginator(items.order_by('medicine_name', 'expiration_date'), 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'inventory/medicine_list.html', {
        'items': page,
        'query': query,
        'state': state,
        'pending_count': pending_count,
    })


@login_required
@staff_required
def medicine_create(request):
    if request.method == 'POST':
        form = MedicineInventoryForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.quantity_available = item.quantity_received
            item.created_by = request.user
            item.save()
            MedicineStockMovement.objects.create(
                medicine=item, movement_type='stock_in',
                quantity=item.quantity_received,
                supplier=item.source_supplier,
                date_received=item.date_received,
                batch_number=item.batch_number,
                expiration_date=item.expiration_date,
                moved_by=request.user,
                notes='Initial stock in',
            )
            AuditLog.objects.create(
                user=request.user, action='STOCK_IN',
                description=f'Added medicine {item.medicine_name} batch {item.batch_number}',
            )
            messages.success(request, 'Medicine added to inventory.')
            return redirect('inventory:medicine_list')
    else:
        form = MedicineInventoryForm()
    return render(request, 'inventory/medicine_form.html', {
        'form': form, 'title': 'Add Medicine Stock'})


@login_required
def medicine_add_stock(request, pk):
    if getattr(request.user, 'role', '') not in ('nurse', 'admin'):
        messages.error(request, 'Not authorized to add medicine stock.')
        return redirect('dashboard:index')
    medicine = get_object_or_404(MedicineInventory, pk=pk)
    if request.method == 'POST':
        form = MedicineAddStockForm(request.POST, medicine=medicine)
        if form.is_valid():
            batch, created, duplicate = form.save(request.user)
            if duplicate:
                messages.info(
                    request,
                    'This stock delivery was already recorded. '
                    'Inventory was not changed.',
                )
                return redirect('inventory:medicine_list')
            quantity = form.cleaned_data['quantity_received']
            supplier = form.cleaned_data['source_supplier']
            AuditLog.objects.create(
                user=request.user, action='STOCK_IN',
                description=(
                    f'Recorded delivery of {quantity} for '
                    f'{batch.medicine_name} batch {batch.batch_number} '
                    f'({supplier})'
                ),
            )
            messages.success(request, 'Stock added successfully.')
            return redirect('inventory:medicine_list')
    else:
        form = MedicineAddStockForm(medicine=medicine)
    return render(request, 'inventory/medicine_add_stock.html', {
        'form': form,
        'medicine': medicine,
    })


@login_required
@staff_required
def medicine_detail(request, pk):
    item = get_object_or_404(MedicineInventory, pk=pk)
    if getattr(request.user, 'role', '') != 'nurse':
        movements = item.movements.select_related('moved_by').order_by('-created_at')
        prescriptions = item.prescriptions.select_related(
            'patient', 'bite_case', 'dispensed_by').order_by('-created_at')[:20]
        return render(request, 'inventory/medicine_detail.html', {
            'item': item, 'movements': movements, 'prescriptions': prescriptions})

    # The nurse view is product-level: every batch with the same name, generic,
    # strength, form and unit contributes to the summary, transactions and
    # dispensing records.
    product_batches = MedicineInventory.objects.filter(
        medicine_name__iexact=item.medicine_name,
        generic_name__iexact=item.generic_name,
        strength__iexact=item.strength,
        dosage_form=item.dosage_form,
        unit__iexact=item.unit,
    )
    totals = product_batches.aggregate(
        received=Sum('quantity_received'),
        dispensed=Sum('quantity_dispensed'),
    )
    movements = (
        MedicineStockMovement.objects
        .filter(medicine__in=product_batches)
        .select_related('moved_by', 'medicine')
        .order_by('-created_at')
    )
    prescriptions = (
        Prescription.objects
        .filter(medicine__in=product_batches)
        .select_related('patient', 'bite_case', 'dispensed_by', 'assessment')
        .order_by('-created_at')
    )

    live_batches = [batch for batch in product_batches if not batch.is_expired()]
    live_available = sum(batch.quantity_available for batch in live_batches)
    totals['available'] = live_available
    reorder_level = max(
        (batch.reorder_level for batch in product_batches), default=0)
    if not live_batches:
        stock_status = 'expired'
    elif live_available <= 0:
        stock_status = 'out'
    elif live_available <= reorder_level:
        stock_status = 'low'
    else:
        stock_status = 'available'

    return render(request, 'inventory/medicine_detail_nurse.html', {
        'item': item,
        'totals': totals,
        'movements': movements,
        'prescriptions': prescriptions,
        'stock_status': stock_status,
    })


@login_required
@staff_required
def prescription_list(request):
    status = request.GET.get('status', 'pending')
    query = request.GET.get('q', '')
    items = Prescription.objects.select_related(
        'patient', 'bite_case', 'medicine', 'dispensed_by', 'assessment__assessed_by')
    if status != 'all':
        items = items.filter(status=status)
    if query:
        items = items.filter(
            Q(patient__first_name__icontains=query) |
            Q(patient__last_name__icontains=query) |
            Q(patient__patient_number__icontains=query) |
            Q(bite_case__case_number__icontains=query) |
            Q(medicine__medicine_name__icontains=query)
        )
    paginator = Paginator(items.order_by('status', '-created_at'), 20)
    page = paginator.get_page(request.GET.get('page'))
    return render(request, 'inventory/prescription_list.html', {
        'items': page,
        'status': status,
        'query': query,
        'can_dispense': can_dispense(request.user),
        'pending_count': Prescription.objects.filter(status='pending').count(),
    })


@login_required
@require_POST
def prescription_dispense(request, pk):
    if not can_dispense(request.user):
        messages.error(request, 'Not authorized to dispense medicines.')
        return redirect('inventory:prescription_list')
    prescription = get_object_or_404(
        Prescription.objects.select_related('patient', 'bite_case', 'medicine'), pk=pk)
    try:
        prescription.dispense(request.user)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        AuditLog.objects.create(
            user=request.user, action='DISPENSE_MEDICINE',
            description=(f'Dispensed {prescription.quantity} {prescription.medicine.unit} of '
                         f'{prescription.medicine.medicine_name} for '
                         f'{prescription.patient.patient_number} '
                         f'({prescription.bite_case.case_number if prescription.bite_case_id else "general"})'),
        )
        messages.success(
            request,
            f'Dispensed {prescription.quantity} {prescription.medicine.unit} of '
            f'{prescription.medicine.medicine_name} for {prescription.patient.full_name()}.',
        )
    from django.utils.http import url_has_allowed_host_and_scheme
    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect('inventory:prescription_list')


@login_required
def supply_list(request):
    items = SupplyInventory.objects.all().order_by('supply_name')
    return render(request, 'inventory/supply_list.html', {'items': items})

@login_required
def supply_create(request):
    if request.method == 'POST':
        form = SupplyInventoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Supply item added.')
            return redirect('inventory:supply_list')
    else:
        form = SupplyInventoryForm()
    return render(request, 'inventory/supply_form.html', {'form': form, 'title': 'Add Supply Item'})
