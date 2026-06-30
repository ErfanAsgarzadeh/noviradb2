from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    fieldsets = UserAdmin.fieldsets + (('OrganizationData',{'fields': ('employee_code', 'job_title'),}),)

admin.site.register(CustomUser, CustomUserAdmin)
