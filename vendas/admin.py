from django.contrib import admin
from .models import Product, Order, Customer, DownloadLink

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "cpf", "email", "phone", "created_at")
    search_fields = ("full_name", "cpf", "email")
    list_filter = ("created_at",)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "price", "active", "created_at")
    search_fields = ("title", "description")
    list_filter = ("active",)
    readonly_fields = ("slug", "checkout_token",)

def marcar_como_pago(modeladmin, request, queryset):
    for o in queryset:
        o.mark_paid()
marcar_como_pago.short_description = "Marcar selecionados como pagos"

def cancelar(modeladmin, request, queryset):
    queryset.filter(status="pending").update(status="cancelled")
cancelar.short_description = "Cancelar selecionados (pendentes)"

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "product", "customer", "amount", "status", "created_at", "expires_at")
    list_filter = ("status", "created_at")
    search_fields = ("customer__full_name", "customer__cpf", "product__title")
    actions = [marcar_como_pago, cancelar]
    readonly_fields = ("preference_id", "external_ref")

@admin.register(DownloadLink)
class DownloadLinkAdmin(admin.ModelAdmin):
    list_display = ("order", "token", "expires_at", "download_count", "max_downloads")
    readonly_fields = ("token",)

from .models import PaymentConfig

from django.contrib import admin
from .models import PaymentConfig, Order

@admin.register(PaymentConfig)
class PaymentConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")

