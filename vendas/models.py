# vendas/models.py
import uuid
from datetime import timedelta
from decimal import Decimal
import re

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

# ---------------------------------------
# Storages do Cloudinary (com fallback local)
# ---------------------------------------
try:
    # cloudinary>=1.41 / django-cloudinary-storage>=0.3.0
    from cloudinary_storage.storage import (
        MediaCloudinaryStorage,
        RawMediaCloudinaryStorage,
    )
    IMAGE_STORAGE_KW = {"storage": MediaCloudinaryStorage()}
    RAW_STORAGE_KW = {"storage": RawMediaCloudinaryStorage()}
except Exception:
    IMAGE_STORAGE_KW = {}
    RAW_STORAGE_KW = {}

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
    token = cache.get("mp_access_token")
    if token:
        return token
    cfg = PaymentConfig.objects.filter(active=True).first()
    token = (cfg.access_token.strip() if (cfg and cfg.access_token) else getattr(settings, "MP_ACCESS_TOKEN", ""))
    cache.set("mp_access_token", token, 60)
    return token


def get_mp_public_key() -> str:
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

def _only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def _format_cnpj(digits: str) -> str:
    if len(digits) != 14:
        return digits
    return f"{digits[0:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:14]}"

def normalize_cep(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 8:
        return f"{d[:5]}-{d[5:]}"
    return v or ""

phone_e164_validator = RegexValidator(
    regex=r"^\+\d{10,15}$",
    message="Use o formato internacional (E.164), ex.: +556300000000",
)

# UF choices para formulários
UF_CHOICES = [
    ("AC","AC"),("AL","AL"),("AP","AP"),("AM","AM"),("BA","BA"),("CE","CE"),
    ("DF","DF"),("ES","ES"),("GO","GO"),("MA","MA"),("MT","MT"),("MS","MS"),
    ("MG","MG"),("PA","PA"),("PB","PB"),("PR","PR"),("PE","PE"),("PI","PI"),
    ("RJ","RJ"),("RN","RN"),("RS","RS"),("RO","RO"),("RR","RR"),("SC","SC"),
    ("SP","SP"),("SE","SE"),("TO","TO"),
]

# ---------------------------------------
# Cliente
# ---------------------------------------
class Customer(models.Model):
    full_name = models.CharField("Nome completo", max_length=160)
    cpf = models.CharField("CPF", max_length=14, unique=True)  # 000.000.000-00
    email = models.EmailField("E-mail")
    phone = models.CharField("Telefone/WhatsApp", max_length=30, blank=True)  # E.164 (+55...)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.cpf})"

    @staticmethod
    def normalize_br_phone(value: str) -> str:
        s = re.sub(r"\D", "", (value or ""))
        if not s:
            return ""
        s = s.lstrip("0")
        core = s[2:] if s.startswith("55") else s
        if len(core) > 11:
            core = core[-11:]
        return f"+55{core}" if core else ""

    def save(self, *args, **kwargs):
        self.phone = self.normalize_br_phone(self.phone)
        super().save(*args, **kwargs)

# ---------------------------------------
# Endereço (para produtos físicos)
# ---------------------------------------
class Address(models.Model):
    customer = models.ForeignKey('Customer', on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField("Apelido (ex.: Casa, Trabalho)", max_length=40, blank=True)
    recipient_name = models.CharField("Nome do destinatário", max_length=160, blank=True)

    cep = models.CharField("CEP", max_length=9)
    street = models.CharField("Logradouro", max_length=160)
    number = models.CharField("Número", max_length=20)
    complement = models.CharField("Complemento", max_length=80, blank=True)
    neighborhood = models.CharField("Bairro", max_length=120)
    city = models.CharField("Cidade", max_length=120)
    state = models.CharField("UF", max_length=2, choices=UF_CHOICES)
    country = models.CharField("País", max_length=60, default="Brasil")

    is_default = models.BooleanField("Endereço padrão?", default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Endereço"
        verbose_name_plural = "Endereços"
        ordering = ["-is_default", "-created_at"]
        indexes = [
            models.Index(fields=["customer", "is_default"]),
            models.Index(fields=["cep"]),
        ]

    def __str__(self):
        base = f"{self.street}, {self.number} - {self.city}/{self.state}"
        return f"{self.label or 'Endereço'} • {base}"

    def clean(self):
        self.cep = normalize_cep(self.cep)

    def save(self, *args, **kwargs):
        # normaliza CEP e garante exclusividade do 'padrão'
        self.cep = normalize_cep(self.cep)
        super().save(*args, **kwargs)
        if self.is_default:
            Address.objects.filter(customer=self.customer).exclude(pk=self.pk).update(is_default=False)

    @property
    def cep_digits(self) -> str:
        return re.sub(r"\D", "", self.cep or "")

    @property
    def full_address(self) -> str:
        comp = f", {self.complement}" if self.complement else ""
        return f"{self.street}, {self.number}{comp} - {self.neighborhood}, {self.city}/{self.state} • {self.cep}"

# ---------------------------------------
# Produto
# ---------------------------------------
class ProductType(models.TextChoices):
    DIGITAL  = "digital", "Digital"
    PHYSICAL = "physical", "Físico"

class Product(models.Model):
    title = models.CharField("Título", max_length=160)
    slug = models.SlugField(unique=True, max_length=180, editable=False)
    checkout_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    description = models.TextField("Descrição", blank=True)

    image = models.ImageField(
        "Imagem",
        upload_to="produtos/imagens/",
        blank=True, null=True,
        **IMAGE_STORAGE_KW,
    )
    video_url = models.URLField("Vídeo (URL)", blank=True)

    digital_file = models.FileField(
        "Arquivo digital",
        upload_to="produtos/arquivos/",
        blank=True, null=True,
        **RAW_STORAGE_KW,
    )

    price = models.DecimalField("Preço (R$)", max_digits=10, decimal_places=2)

    promo_active = models.BooleanField("Promoção ativa?", default=False)
    promo_price = models.DecimalField(
        "Preço promocional (R$)", max_digits=10, decimal_places=2, blank=True, null=True
    )

    product_type = models.CharField(
        "Tipo de produto",
        max_length=10,
        choices=ProductType.choices,
        default=ProductType.DIGITAL,
        db_index=True,
    )

    active = models.BooleanField("Ativo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["active", "product_type", "created_at"])]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:70]
            self.slug = f"{base}-{uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    def get_checkout_url(self):
        return reverse("checkout", args=[self.slug, str(self.checkout_token)])

    def __str__(self):
        return self.title

    @property
    def has_promo(self) -> bool:
        return bool(self.promo_active and self.promo_price is not None and self.promo_price < self.price)

    @property
    def price_to_charge(self):
        return self.promo_price if self.has_promo else self.price

    @property
    def discount_amount(self) -> Decimal:
        try:
            if self.has_promo and self.price and self.promo_price is not None:
                return (self.price - self.promo_price).copy_abs()
        except Exception:
            pass
        return Decimal("0.00")

    @property
    def discount_percent(self) -> int:
        try:
            if self.has_promo and self.price and self.price > 0:
                pct = (Decimal("1") - (self.price_to_charge / self.price)) * 100
                return int(pct.quantize(Decimal("1")))
        except Exception:
            pass
        return 0

    # Sinônimos p/ compatibilidade
    @property
    def promo_percent(self) -> int: return self.discount_percent
    @property
    def promo_pct(self) -> int:     return self.discount_percent
    @property
    def discount_pct(self) -> int:  return self.discount_percent

    @property
    def is_physical(self) -> bool:
        return self.product_type == ProductType.PHYSICAL

    @property
    def video_embed_url(self):
        url = (self.video_url or "").strip()
        if not url:
            return ""
        if "youtube.com/watch" in url or "youtu.be/" in url:
            vid = None
            if "watch?v=" in url:
                vid = url.split("watch?v=", 1)[-1].split("&", 1)[0]
            else:
                m = re.search(r"youtu\.be/([^?&/]+)", url)
                vid = m.group(1) if m else None
            return f"https://www.youtube.com/embed/{vid}" if vid else url
        if "vimeo.com/" in url:
            vid = url.rstrip("/").split("/")[-1]
            return f"https://player.vimeo.com/video/{vid}"
        return url

# ---------------------------------------
# Pedido (Pix + Cartão)
# ---------------------------------------
# vendas/models.py
from django.db import models
# ... imports já existentes ...

class ShippingStatus(models.TextChoices):
    PENDING = "pending", "Pendente de envio"
    SHIPPED = "shipped", "Enviado"

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

    # Endereço de envio (quando produto físico)
    shipping_address = models.ForeignKey(
        Address, on_delete=models.PROTECT, null=True, blank=True, related_name="orders"
    )

    # ✅ Status de envio
    shipping_status = models.CharField(
        "Status de envio",
        max_length=12,
        choices=ShippingStatus.choices,
        default=ShippingStatus.PENDING,
        db_index=True,
    )

    # Rastreio / envio (campos que você já tinha adicionado)
    tracking_code = models.CharField("Código de rastreio", max_length=80, blank=True)
    tracking_carrier = models.CharField("Transportadora/Serviço", max_length=40, blank=True)
    shipped_at = models.DateTimeField("Enviado em", null=True, blank=True)

    # Gateway (Mercado Pago)
    gateway = models.CharField(max_length=40, default="mercadopago")
    preference_id = models.CharField(max_length=120, blank=True)
    external_ref = models.CharField(max_length=120, blank=True, help_text="Ex: order-<id>")

    # PIX (Payments API)
    payment_id = models.CharField("MP Payment ID", max_length=64, blank=True, null=True, unique=True)
    pix_qr_code = models.TextField("Pix copia-e-cola", blank=True)
    pix_qr_base64 = models.TextField("QR base64", blank=True)
    pix_ticket_url = models.URLField("Ticket URL", blank=True)

    # Cartão (metadados leves)
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
            models.Index(fields=["customer", "created_at"]),
            models.Index(fields=["shipping_status"]),  # ✅ índice p/ filtrar no admin
        ]

    def __str__(self):
        return f"Pedido #{self.pk} - {self.product.title} - {self.get_status_display()}"

    @property
    def is_pending(self):
        return self.status == "pending"

    @property
    def is_expired(self):
        return self.is_pending and timezone.now() > self.expires_at

    @property
    def needs_shipping(self) -> bool:
        try:
            return self.product.product_type == ProductType.PHYSICAL
        except Exception:
            return False

    def clean(self):
        # Regras de consistência simples
        if self.product and self.product.product_type == ProductType.PHYSICAL and not self.shipping_address:
            raise ValidationError({"shipping_address": "Informe o endereço de envio para produtos físicos."})
        if self.product and self.product.product_type == ProductType.DIGITAL and self.shipping_address:
            raise ValidationError({"shipping_address": "Produtos digitais não devem ter endereço de envio."})

    # ✅ Helpers de envio
    def mark_shipped(self, *, tracking_code=None, carrier=None, when=None, save=True):
        """
        Marca como ENVIADO e preenche rastreio/opcionalmente data.
        """
        self.shipping_status = ShippingStatus.SHIPPED
        if tracking_code is not None:
            self.tracking_code = tracking_code
        if carrier is not None:
            self.tracking_carrier = carrier
        if when is None:
            when = timezone.now()
        self.shipped_at = when
        if save:
            self.save(update_fields=["shipping_status", "tracking_code", "tracking_carrier", "shipped_at"])

    def mark_pending_shipping(self, *, save=True):
        """
        Volta para PENDENTE DE ENVIO (sem apagar rastreio).
        """
        self.shipping_status = ShippingStatus.PENDING
        if save:
            self.save(update_fields=["shipping_status"])

    def mark_paid(self):
        """
        Marca como pago. Cria link de download apenas para produtos digitais.
        """
        if self.status != "paid":
            self.status = "paid"
            self.save(update_fields=["status"])
        if self.product and self.product.product_type == ProductType.DIGITAL:
            try:
                _ = self.download_link
            except DownloadLink.DoesNotExist:
                DownloadLink.create_for_order(self)
        try:
            from .emails import send_order_paid_email
            send_order_paid_email(self)
        except Exception:
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
# Link de download (apenas útil para digitais)
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

# ---------------------------------------
# Empresa
# ---------------------------------------
class Company(models.Model):
    corporate_name = models.CharField("Razão social", max_length=160)
    trade_name     = models.CharField("Nome fantasia", max_length=160, blank=True)
    cnpj           = models.CharField("CNPJ", max_length=18, unique=True,
                                      help_text="Ex.: 12.345.678/0001-95")
    address        = models.TextField("Endereço", blank=True)
    phone_e164     = models.CharField(
        "Telefone (E.164)", max_length=20, blank=True,
        validators=[phone_e164_validator],
        help_text="Ex.: +556300000000",
    )
    logo = models.ImageField(
        "Logo",
        upload_to="empresa/logos/",
        blank=True, null=True,
        **(globals().get("IMAGE_STORAGE_KW", {})),
    )

    active     = models.BooleanField("Ativa?", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ["-created_at"]

    def __str__(self):
        return self.trade_name or self.corporate_name

    def clean(self):
        d = _only_digits(self.cnpj)
        if len(d) != 14:
            raise ValidationError({"cnpj": "CNPJ deve ter 14 dígitos."})
        self.cnpj = _format_cnpj(d)

    @property
    def logo_url(self):
        try:
            return self.logo.url if self.logo else ""
        except Exception:
            return ""

    @property
    def phone_display(self) -> str:
        d = re.sub(r"\D", "", self.phone_e164 or "")
        if not d:
            return ""
        core = d[2:] if d.startswith("55") else d
        if len(core) == 11:
            ddd, n1, n2 = core[:2], core[2:7], core[7:]
            return f"({ddd}) {n1}-{n2}"
        if len(core) == 10:
            ddd, n1, n2 = core[:2], core[2:6], core[6:]
            return f"({ddd}) {n1}-{n2}"
        return self.phone_e164 or ""

    @property
    def whatsapp_link(self) -> str:
        d = re.sub(r"\D", "", self.phone_e164 or "")
        return f"https://wa.me/{d}" if d else ""
