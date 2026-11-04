from django.db import models
from django.conf import settings

class AuditLog(models.Model):
    TARGET_TYPES = [
        ('patient', 'Patient'),
        ('bite_case', 'Bite Case'),
        ('pre_registration', 'Online Registration'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50)
    description = models.TextField()
    target_type = models.CharField(max_length=30, blank=True, default='', choices=TARGET_TYPES)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"

    def __str__(self):
        return f"{self.user} - {self.action} - {self.created_at}"
