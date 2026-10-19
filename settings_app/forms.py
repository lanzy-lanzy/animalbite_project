from django import forms
from .models import SystemSetting

class SystemSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = '__all__'
        widgets = {
            'key': forms.TextInput(attrs={'class': 'form-input'}),
            'value': forms.Textarea(attrs={'rows': 3, 'class': 'form-textarea'}),
            'description': forms.Textarea(attrs={'rows': 2, 'class': 'form-textarea'}),
        }
