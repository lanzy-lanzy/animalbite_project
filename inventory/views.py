from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from datetime import date
from .models import VaccineInventory, SupplyInventory, VaccineStockMovement
from .forms import VaccineInventoryForm, SupplyInventoryForm, VaccineStockMovementForm
from audit.models import AuditLog

@login_required
def vaccine_list(request):
    query = request.GET.get('q', '')
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
                moved_by=request.user, notes='Initial stock in'
            )
            AuditLog.objects.create(user=request.user, action='STOCK_IN', description=f'Added vaccine {item.vaccine_name} batch {item.batch_number}')
            messages.success(request, 'Vaccine inventory added.')
            return redirect('inventory:vaccine_list')
    else:
        form = VaccineInventoryForm()
    return render(request, 'inventory/vaccine_form.html', {'form': form, 'title': 'Add Vaccine Stock'})

@login_required
def vaccine_detail(request, pk):
    item = get_object_or_404(VaccineInventory, pk=pk)
    movements = item.movements.all().order_by('-created_at')
    return render(request, 'inventory/vaccine_detail.html', {'item': item, 'movements': movements})

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
