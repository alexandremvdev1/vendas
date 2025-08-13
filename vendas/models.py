import uuid
import re
from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from django.core.cache import cache
from cloudinary.models import CloudinaryField

# ===== Cloudinary storages (opcional, com fallback local) =====
# Observação: este bloco é usado APENAS se você optar por ImageField/FileField
# com CloudinaryStorage (alternativa ao CloudinaryField). Mantido aqui para
# compatibilidade futura, mas NÃO é utilizado no código abaixo.
try:
    # pip install cloudinary django-cloudinary-storage
    from cloudinary_storage.storage import MediaCloudinaryStorage, RawMediaCloudinaryStorage
    MEDIA_STORAGE = MediaCloudinaryStorage()
    RAW_STORAGE = RawMediaCloudinaryStorage()
except Exception:
    MEDIA_STORAGE = None
    RAW_STORAGE = None


# ---------------------------------------
# Credenciais do gateway (opcional, recomendado)
# ---------------------------------------
class PaymentConfig(models.Model):
    name = models.CharField("Nome da credencial", max_length=80, default="Padrão")
    access_token = models.TextField("Access Token (Mercado Pago)")
    public_key = models.CharField("Public Key (opcional)", max_length=120, blank=True)
    webhook_secret = models.CharField("Webhook secret (opcional)", max_length=120, blank=True)
    active = models.BooleanField("Ativa?", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Credencial de Pagamento"
        verbose_name_plural = "Credenciais de Pagamento"

    def __str__(self):
        return f"{self.name} ({'ativa' if self.active else 'inativa'})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.active:
            PaymentConfig.objects.exclude(pk=self.pk).update(active=False)
        cache.delete("mp_access_token")
        cache.delete("mp_public_key")


def get_mp_access_token() -> str:
    """
    Pega o Access Token da credencial ativa (PaymentConfig) ou, em fallback, do settings.MP_ACCESS_TOKEN.
    Cache básico por 60s.
    """
    token = cache.get("mp_access_token")
    if token:
        return token
    cfg = PaymentConfig.objects.filter(active=True).first()
    token = (cfg.access_token.strip() if (cfg and cfg.access_token) else getattr(settings, "MP_ACCESS_TOKEN", ""))
    cache.set("mp_access_token", token, 60)
    return token


def get_mp_public_key() -> str:
    """
    Public Key usada nos Bricks/Checkout. Busca na credencial ativa, senão em settings.MP_PUBLIC_KEY.
    Cache básico por 60s.
    """
    pk = cache.get("mp_public_key")
    if pk:
        return pk
    cfg = PaymentConfig.objects.filter(active=True).first()
    pk = (cfg.public_key.strip() if (cfg and cfg.public_key) else getattr(settings, "MP_PUBLIC_KEY", ""))
    cache.set("mp_public_key", pk, 60)
    return pk


# ---------------------------------------
# Helpers
# ---------------------------------------
def default_order_expiry():
    return timezone.now() + timedelta(days=2)


# ---------------------------------------
# Cliente
# ---------------------------------------
class Customer(models.Model):
    full_name = models.CharField("Nome completo", max_length=160)
    cpf = models.CharField("CPF", max_length=14, unique=True)  # salvo formatado 000.000.000-00
    email = models.EmailField("E-mail")
    phone = models.CharField("Telefone/WhatsApp", max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.cpf})"


# ---------------------------------------
# Produto
# ---------------------------------------
class Product(models.Model):
    title = models.CharField("Título", max_length=160)
    slug = models.SlugField(unique=True, max_length=180, editable=False)
    checkout_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    description = models.TextField("Descrição", blank=True)

    # Imagem hospedada no Cloudinary (resource_type padrão: "image")
    # Atenção: não usar argumento posicional (evita duplicar verbose_name).
    image = CloudinaryField(
        verbose_name="Imagem",
        folder="produtos/imagens",
        null=True,
        blank=True,
    )

    video_url = models.URLField("Vídeo (URL)", blank=True)

    # Arquivo digital (PDF/ZIP etc.) hospedado no Cloudinary como resource_type="raw"
    digital_file = CloudinaryField(
        verbose_name="Arquivo digital",
        folder="produtos/arquivos",
        resource_type="raw",
        null=True,
        blank=True,
    )

    price = models.DecimalField("Preço (R$)", max_digits=10, decimal_places=2)
    active = models.BooleanField("Ativo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:70]
            self.slug = f"{base}-{uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    def get_checkout_url(self):
        return reverse("checkout", args=[self.slug, str(self.checkout_token)])

    def __str__(self):
        return self.title


# ---------------------------------------
# Pedido (Pix + Cartão) - UMA ÚNICA CLASSE
# ---------------------------------------
class Order(models.Model):
    STATUS = [
        ("pending", "Pendente"),
        ("paid", "Pago"),
        ("cancelled", "Cancelado"),
    ]
    PAYMENT_TYPE = [
        ("pix", "Pix"),
        ("card", "Cartão"),
    ]

    product = models.ForeignKey('Product', on_delete=models.PROTECT, related_name="orders")
    customer = models.ForeignKey('Customer', on_delete=models.PROTECT, related_name="orders")
    amount = models.DecimalField("Valor", max_digits=10, decimal_places=2)

    status = models.CharField("Status", max_length=10, choices=STATUS, default="pending", db_index=True)
    payment_type = models.CharField("Forma de pagamento", max_length=10, choices=PAYMENT_TYPE, default="pix", db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("Expira em", default=default_order_expiry)

    # Gateway (Mercado Pago)
    gateway = models.CharField(max_length=40, default="mercadopago")
    preference_id = models.CharField(max_length=120, blank=True)
    external_ref = models.CharField(max_length=120, blank=True, help_text="Ex: order-<id>")

    # IDs/dados Pix (quando for Pix via /v1/payments)
    payment_id = models.CharField("MP Payment ID", max_length=64, blank=True, null=True, unique=True)
    pix_qr_code = models.TextField("Pix copia-e-cola", blank=True)
    pix_qr_base64 = models.TextField("QR base64", blank=True)
    pix_ticket_url = models.URLField("Ticket URL", blank=True)

    # Metadados não-sensíveis de cartão (quando for cartão)
    installments = models.PositiveIntegerField("Parcelas", default=1)
    card_brand = models.CharField("Bandeira", max_length=20, blank=True)
    card_last4 = models.CharField("Final", max_length=4, blank=True)
    card_holder = models.CharField("Titular", max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["payment_type"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["product", "status"]),
        ]

    def __str__(self):
        return f"Pedido #{self.pk} - {self.product.title} - {self.get_status_display()}"

    @property
    def is_pending(self):
        return self.status == "pending"

    @property
    def is_expired(self):
        return self.is_pending and timezone.now() > self.expires_at

    def mark_paid(self):
        """
        Marca como pago, garante o link de download e dispara o e-mail de confirmação.
        """
        if self.status != "paid":
            self.status = "paid"
            self.save(update_fields=["status"])
            if not hasattr(self, "download_link"):
                DownloadLink.create_for_order(self)
            # envia o e-mail de pagamento confirmado
            try:
                from .emails import send_order_paid_email  # import local p/ evitar circular
                send_order_paid_email(self)
            except Exception:
                # não deixa o fluxo de pagamento falhar por causa de e-mail
                pass

    def mark_cancelled(self):
        if self.status != "cancelled":
            self.status = "cancelled"
            self.save(update_fields=["status"])

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and not self.external_ref:
            self.external_ref = f"order-{self.pk}"
            super().save(update_fields=["external_ref"])


# ---------------------------------------
# Link de download
# ---------------------------------------
class DownloadLink(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="download_link")
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    expires_at = models.DateTimeField()
    download_count = models.PositiveIntegerField(default=0)
    max_downloads = models.PositiveIntegerField(default=5)

    def is_valid(self):
        return (
            self.order.status == "paid"
            and self.download_count < self.max_downloads
            and timezone.now() < self.expires_at
        )

    @classmethod
    def create_for_order(cls, order, days_valid=7):
        return cls.objects.create(order=order, expires_at=timezone.now() + timedelta(days=days_valid))
