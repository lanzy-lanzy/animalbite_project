from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from .models import SystemSetting
from .forms import SystemSettingForm

def is_admin(user):
    return user.is_authenticated and user.role == 'admin'

@login_required
@user_passes_test(is_admin)
def settings_list(request):
    settings = SystemSetting.objects.all().order_by('key')
    return render(request, 'settings_app/settings_list.html', {'settings': settings})

@login_required
@user_passes_test(is_admin)
def settings_edit(request, pk):
    setting = get_object_or_404(SystemSetting, pk=pk)
    if request.method == 'POST':
        form = SystemSettingForm(request.POST, instance=setting)
        if form.is_valid():
            form.save()
            messages.success(request, 'Setting updated successfully.')
            return redirect('settings_app:settings_list')
    else:
        form = SystemSettingForm(instance=setting)
    return render(request, 'settings_app/settings_form.html', {'form': form, 'setting': setting})
