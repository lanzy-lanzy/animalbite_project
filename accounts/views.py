from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm
from .forms import LoginForm, ProfileEditForm, UserCreateForm, UserEditForm

User = get_user_model()

def is_admin(user):
    return user.is_authenticated and user.role == 'admin'

def landing_view(request):
    if request.user.is_authenticated:
        if request.user.role == 'patient':
            return redirect('accounts:patient_portal')
        return redirect('dashboard:index')
    return render(request, 'accounts/landing.html')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:index')
    form = LoginForm()
    if request.method == 'POST':
        form = LoginForm(data=request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(username=username, password=password)
            if user and user.is_active:
                login(request, user)
                from audit.models import AuditLog
                AuditLog.objects.create(user=user, action='LOGIN', description=f'User {user.username} logged in')
                messages.success(request, f'Welcome back, {user.get_full_name() or user.username}!')
                if user.role == 'patient':
                    return redirect('accounts:patient_portal')
                return redirect('dashboard:index')
            else:
                messages.error(request, 'Account is inactive.')
        else:
            messages.error(request, 'Invalid username or password.')
    return render(request, 'accounts/login.html', {'form': form})

def logout_view(request):
    if request.user.is_authenticated:
        from audit.models import AuditLog
        AuditLog.objects.create(user=request.user, action='LOGOUT', description=f'User {request.user.username} logged out')
    logout(request)
    return redirect('accounts:login')

@login_required
def profile_view(request):
    return render(request, 'accounts/profile.html', {'user_obj': request.user})

@login_required
def account_settings(request):
    """Unified account settings page — profile info + quick actions."""
    return render(request, 'accounts/account_settings.html', {'user_obj': request.user})

@login_required
def profile_edit(request):
    user = request.user
    if request.method == 'POST':
        form = ProfileEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('accounts:profile')
    else:
        form = ProfileEditForm(instance=user)
    return render(request, 'accounts/profile_edit.html', {'form': form})


@login_required
def patient_portal(request):
    if request.user.role != 'patient':
        return redirect('dashboard:index')

    from vaccination.models import VaccineDose

    pre_registrations = request.user.pre_registrations.select_related(
        'converted_case', 'converted_patient'
    ).order_by('-submitted_at')
    cases = request.user.patient_profile.bite_cases.all().order_by('-created_at') if hasattr(request.user, 'patient_profile') else []
    doses = VaccineDose.objects.filter(
        schedule__bite_case__patient__account=request.user
    ).select_related('schedule__bite_case').order_by('scheduled_date')
    return render(request, 'accounts/patient_portal.html', {
        'pre_registrations': pre_registrations,
        'cases': cases,
        'doses': doses,
    })

@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        # Style widgets to match app design system
        for fname, field in form.fields.items():
            field.widget.attrs.update({
                'class': 'form-input pr-10',
                'placeholder': '••••••••',
                'autocomplete': 'new-password' if 'new' in fname else 'current-password',
            })
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Password changed successfully.')
            return redirect('accounts:profile')
    else:
        form = PasswordChangeForm(request.user)
        for fname, field in form.fields.items():
            field.widget.attrs.update({
                'class': 'form-input pr-10',
                'placeholder': '••••••••',
                'autocomplete': 'new-password' if 'new' in fname else 'current-password',
            })
            if fname == 'old_password':
                field.widget.attrs['autocomplete'] = 'current-password'
                field.widget.attrs['placeholder'] = 'Current password'
            if fname == 'new_password1':
                field.widget.attrs['placeholder'] = 'New password (min 8 chars)'
            if fname == 'new_password2':
                field.widget.attrs['placeholder'] = 'Confirm new password'
    return render(request, 'accounts/change_password.html', {'form': form})

@login_required
@user_passes_test(is_admin)
def user_list(request):
    users = User.objects.all().order_by('-date_joined')
    return render(request, 'accounts/user_list.html', {'users': users})

@login_required
@user_passes_test(is_admin)
def user_create(request):
    if request.method == 'POST':
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            from audit.models import AuditLog
            AuditLog.objects.create(user=request.user, action='CREATE_USER', description=f'Created user {user.username}')
            messages.success(request, f'User {user.username} created successfully.')
            return redirect('accounts:user_list')
    else:
        form = UserCreateForm()
    return render(request, 'accounts/user_form.html', {'form': form, 'title': 'Add User'})

@login_required
@user_passes_test(is_admin)
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            from audit.models import AuditLog
            AuditLog.objects.create(user=request.user, action='EDIT_USER', description=f'Edited user {user.username}')
            messages.success(request, f'User {user.username} updated successfully.')
            return redirect('accounts:user_list')
    else:
        form = UserEditForm(instance=user)
    return render(request, 'accounts/user_form.html', {'form': form, 'title': 'Edit User'})

@login_required
@user_passes_test(is_admin)
def user_toggle_active(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, 'You cannot deactivate your own account.')
        return redirect('accounts:user_list')
    user.is_active = not user.is_active
    user.save()
    status = 'activated' if user.is_active else 'deactivated'
    from audit.models import AuditLog
    AuditLog.objects.create(user=request.user, action='TOGGLE_USER', description=f'{status} user {user.username}')
    messages.success(request, f'User {user.username} {status}.')
    return redirect('accounts:user_list')
