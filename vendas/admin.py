# vendas/admin.py
from django.contrib import admin
from .models import (
    Product, Order, Customer, DownloadLink,
    PaymentConfig, Company, Address
)

# ===== Ações em massa =====
@admin.action(description="Marcar selecionados como pagos")
def marcar_como_pago(modeladmin, request, queryset):
    for o in queryset:
        o.mark_paid()

@admin.action(description="Cancelar selecionados (apenas pendentes)")
def cancelar(modeladmin, request, queryset):
    queryset.filter(status="pending").update(status="cancelled")


# ===== Customer =====
@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "cpf", "email", "phone", "created_at")
    search_fields = ("full_name", "cpf", "email", "phone")
    list_filter = ("created_at",)
    readonly_fields = ("created_at",)


# ===== Product =====
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "product_type", "price", "promo_active", "active", "created_at")
    list_filter = ("product_type", "promo_active", "active", "created_at")
    search_fields = ("title", "description")
    readonly_fields = ("slug", "checkout_token", "created_at")
    autocomplete_fields = ()
    # slug é gerado no save(); checkout_token é UUID somente leitura


# ===== Address =====
@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display  = ("id", "customer", "label_display", "cep", "city", "state", "is_default", "created_at")
    list_filter   = ("is_default", "state", "city", "created_at")
    search_fields = ("customer__full_name", "cep", "street", "neighborhood", "city", "label")
    autocomplete_fields = ("customer",)
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (None, {
            "fields": ("customer", "label", "recipient_name", "is_default")
        }),
        ("Endereço", {
            "fields": ("cep", "street", "number", "complement", "neighborhood", "city", "state", "country")
        }),
        ("Metadados", {
            "fields": ("created_at", "updated_at")
        }),
    )

    def label_display(self, obj):
        return obj.label or "—"
    label_display.short_description = "Apelido"


# ===== Order =====
# vendas/admin.py
from django.contrib import admin
from .models import Order, ShippingStatus

def acao_marcar_enviado(modeladmin, request, queryset):
    for o in queryset:
        o.mark_shipped(save=True)
acao_marcar_enviado.short_description = "Marcar como ENVIADO"

def acao_marcar_pendente_envio(modeladmin, request, queryset):
    queryset.update(shipping_status=ShippingStatus.PENDING)
acao_marcar_pendente_envio.short_description = "Marcar como PENDENTE DE ENVIO"

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id", "product", "customer", "amount",
        "status",                 # pagamento
        "shipping_status",        # ✅ envio
        "tracking_code", "tracking_carrier", "shipped_at",
        "created_at", "expires_at",
    )
    list_filter = ("status", "shipping_status", "created_at", "tracking_carrier")
    search_fields = ("customer__full_name", "customer__cpf", "product__title", "tracking_code")
    actions = [acao_marcar_enviado, acao_marcar_pendente_envio]

    readonly_fields = (
        "preference_id", "external_ref",
        "created_at",
        # úteis como somente leitura (se preferir)
        "payment_id", "pix_qr_code", "pix_qr_base64", "pix_ticket_url",
    )

    fieldsets = (
        (None, {
            "fields": ("product", "customer", "amount", "status", "payment_type")
        }),
        ("Envio", {
            "fields": ("shipping_address", "shipping_status", "tracking_code", "tracking_carrier", "shipped_at"),
        }),
        ("Gateway", {
            "fields": ("gateway", "preference_id", "external_ref")
        }),
        ("Pix", {
            "fields": ("payment_id", "pix_qr_code", "pix_qr_base64", "pix_ticket_url")
        }),
        ("Cartão", {
            "fields": ("installments", "card_brand", "card_last4", "card_holder")
        }),
        ("Datas", {
            "fields": ("created_at", "expires_at")
        }),
    )



# ===== DownloadLink =====
@admin.register(DownloadLink)
class DownloadLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "order", "token", "expires_at", "download_count", "max_downloads")
    search_fields = ("order__id", "token")
    readonly_fields = ("token",)


# ===== PaymentConfig =====
@admin.register(PaymentConfig)
class PaymentConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


# ===== Company =====
@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("id", "trade_name", "corporate_name", "cnpj", "phone_e164", "active", "created_at")
    list_filter  = ("active", "created_at")
    search_fields = ("trade_name", "corporate_name", "cnpj")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Dados da empresa", {
            "fields": ("corporate_name", "trade_name", "cnpj", "address", "phone_e164", "logo")
        }),
        ("Status", {
            "fields": ("active", "created_at", "updated_at")
        }),
    )
