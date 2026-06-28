from django import forms
from .models import SystemSetting

class SystemSettingForm(forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = '__all__'
        widgets = {
            'key': forms.TextInput(attrs={'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'value': forms.Textarea(attrs={'rows': 3, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
            'description': forms.Textarea(attrs={'rows': 2, 'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-[#8A0303] focus:border-transparent'}),
        }
