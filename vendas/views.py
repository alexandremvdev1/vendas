# vendas/views.py — imports limpos
import os
import re
import json
import logging
from decimal import Decimal
from datetime import timedelta, datetime, time
from urllib.parse import urlparse

import mercadopago
from cloudinary.utils import private_download_url, cloudinary_url

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, IntegerField, Q, Sum, Value as V
from django.db.models.functions import Coalesce, TruncDate
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

from .forms import CheckoutForm
from .emails import (
    send_order_created_email,
    send_payment_reminder_email,
    send_order_shipped_email,
)
from .models import (
    Product, Customer, Order, DownloadLink, Company,
    Address, ProductType, ShippingStatus,
    get_mp_access_token, get_mp_public_key,
)

logger = logging.getLogger(__name__)

# -------------------- Helpers de preço (promoção) --------------------
def _effective_price(product: Product) -> Decimal:
    promo_price = (
        getattr(product, "promo_price", None)
        or getattr(product, "promotional_price", None)
        or getattr(product, "price_promo", None)
    )
    promo_flag = (
        getattr(product, "promo_active", None)
        if hasattr(product, "promo_active") else
        getattr(product, "is_promo", None)
        if hasattr(product, "is_promo") else
        getattr(product, "is_on_promo", None)
        if hasattr(product, "is_on_promo") else
        None
    )
    if promo_price and (promo_flag is None or bool(promo_flag) is True):
        return Decimal(promo_price)
    return Decimal(product.price)

# -------------------- Mercado Pago: helpers --------------------
def mp_sdk():
    token = get_mp_access_token()
    if not token:
        logger.error("MP Access Token AUSENTE. Configure PaymentConfig ativa ou MP_ACCESS_TOKEN no settings.")
        raise RuntimeError("MP Access Token ausente")
    return mercadopago.SDK(token)

def _ensure_external_ref(order: Order):
    if not order.external_ref:
        order.external_ref = f"order-{order.pk}"
        order.save(update_fields=["external_ref"])

def build_mp_notification_url(request) -> str:
    configured = getattr(settings, "MP_WEBHOOK_URL", "").strip()
    if configured:
        return configured
    try:
        url = request.build_absolute_uri(reverse("mp_webhook"))
    except Exception:
        return ""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https":
        return ""
    if host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
        return ""
    return url

# -------------------- PIX (Payments API) --------------------
def create_pix_payment(product, customer, order, request):
    _ensure_external_ref(order)
    if order.payment_id:
        logger.info("PIX: pulando criação, order %s já possui payment_id %s", order.id, order.payment_id)
        return {"skipped": True, "reason": "already_has_payment_id"}

    current_price = _effective_price(product)
    if order.amount != current_price:
        order.amount = current_price
        order.save(update_fields=["amount"])

    sdk = mp_sdk()
    cpf_digits = re.sub(r"\D", "", customer.cpf or "")
    notification_url = build_mp_notification_url(request)

    payer = {
        "email": customer.email or "",
        "first_name": (customer.full_name.split(" ")[0] or customer.full_name),
        "last_name": " ".join(customer.full_name.split(" ")[1:]) or customer.full_name,
    }
    if len(cpf_digits) == 11:
        payer["identification"] = {"type": "CPF", "number": cpf_digits}

    body = {
        "transaction_amount": float(order.amount),
        "description": product.title,
        "payment_method_id": "pix",
        "external_reference": order.external_ref,
        "payer": payer,
    }
    if notification_url:
        body["notification_url"] = notification_url

    result = sdk.payment().create(body)
    status_code = result.get("status")
    resp = result.get("response", {}) or {}

    logger.info("MP payment.create status=%s order=%s resp=%s",
                status_code, order.id, json.dumps(resp, ensure_ascii=False)[:1500])

    if status_code not in (200, 201):
        msg = resp.get("message") or resp.get("error") or "erro_desconhecido"
        logger.error("Falha ao criar PIX no MP (order %s): %s", order.id, msg)
        raise RuntimeError(f"MP create error {status_code}: {msg}")

    tx = (resp.get("point_of_interaction") or {}).get("transaction_data") or {}
    qr_code = tx.get("qr_code") or ""
    qr_b64 = tx.get("qr_code_base64") or ""
    ticket = tx.get("ticket_url") or ""

    if isinstance(qr_b64, str) and qr_b64.startswith("data:image"):
        qr_b64 = qr_b64.split(",", 1)[-1]
    qr_b64 = re.sub(r"\s+", "", qr_b64 or "")

    order.payment_id = str(resp.get("id") or "")
    order.pix_qr_code = qr_code
    order.pix_qr_base64 = qr_b64
    order.pix_ticket_url = ticket
    order.save(update_fields=["payment_id", "pix_qr_code", "pix_qr_base64", "pix_ticket_url"])
    return resp

def _refresh_pix_from_mp(order: Order):
    if not order.payment_id:
        return False
    sdk = mp_sdk()
    result = sdk.payment().get(order.payment_id)
    status_code = result.get("status")
    res = result.get("response", {}) or {}

    logger.info("MP payment.get status=%s order=%s resp=%s",
                status_code, order.id, json.dumps(res, ensure_ascii=False)[:1500])

    if status_code not in (200, 201):
        logger.warning("Falha ao consultar pagamento MP (order %s): %s", order.id, res)
        return False

    status = (res.get("status") or "").lower()
    if order.status == "pending":
        if status == "approved":
            order.mark_paid()
        elif status in {"rejected", "cancelled", "canceled"}:
            order.mark_cancelled()

    tx = (res.get("point_of_interaction") or {}).get("transaction_data") or {}
    qr_code = tx.get("qr_code") or ""
    qr_b64 = tx.get("qr_code_base64") or ""
    ticket = tx.get("ticket_url") or ""

    if isinstance(qr_b64, str) and qr_b64.startswith("data:image"):
        qr_b64 = qr_b64.split(",", 1)[-1]
    qr_b64 = re.sub(r"\s+", "", qr_b64 or "")

    updates = []
    if qr_code and qr_code != order.pix_qr_code:
        order.pix_qr_code = qr_code; updates.append("pix_qr_code")
    if qr_b64 and qr_b64 != order.pix_qr_base64:
        order.pix_qr_base64 = qr_b64; updates.append("pix_qr_base64")
    if ticket and ticket != order.pix_ticket_url:
        order.pix_ticket_url = ticket; updates.append("pix_ticket_url")

    if updates:
        order.save(update_fields=updates)
        return True
    return False

# -------------------- Checkout Pro (Cartão) --------------------
def create_card_preference(product, customer, order, request):
    _ensure_external_ref(order)
    sdk = mp_sdk()

    return_base = request.build_absolute_uri(reverse("mp_return"))
    success_url = return_base
    pending_url = return_base
    failure_url = return_base

    notification_url = build_mp_notification_url(request)

    payer = {
        "name": (customer.full_name.split(" ")[0] or customer.full_name),
        "surname": " ".join(customer.full_name.split(" ")[1:]) or customer.full_name,
        "email": customer.email or "",
        "phone": {"number": re.sub(r"\D", "", customer.phone or "")[:15]},
        "identification": {"type": "CPF", "number": re.sub(r"\D", "", customer.cpf or "")[:14]},
    }

    pref_body = {
        "items": [{
            "title": product.title,
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": float(order.amount),
            "description": f"Pedido {order.external_ref}"
        }],
        "payer": payer,
        "external_reference": order.external_ref,
        "back_urls": {"success": success_url, "pending": pending_url, "failure": failure_url},
        "auto_return": "approved",
        "expires": True,
        "expiration_date_to": order.expires_at.isoformat(),
        "payment_methods": {
            "excluded_payment_types": [
                {"id": "ticket"}, {"id": "atm"}, {"id": "bank_transfer"},
                {"id": "debit_card"}, {"id": "prepaid_card"}
            ],
        },
        "statement_descriptor": "LOJA DIGITAL",
    }
    if notification_url:
        pref_body["notification_url"] = notification_url

    if order.preference_id:
        try:
            res = sdk.preference().get(order.preference_id)
            resp = res.get("response", {}) or {}
            url = resp.get("init_point") or resp.get("sandbox_init_point")
            if url:
                return url
        except Exception:
            pass

    res = sdk.preference().create(pref_body)
    status_code = res.get("status")
    resp = res.get("response", {}) or {}
    logger.info("MP preference.create status=%s order=%s resp=%s",
                status_code, order.id, json.dumps(resp, ensure_ascii=False)[:1500])

    if status_code not in (200, 201):
        msg = resp.get("message") or resp.get("error") or "erro_desconhecido"
        raise RuntimeError(f"MP preference error {status_code}: {msg}")

    order.preference_id = str(resp.get("id") or "")
    order.save(update_fields=["preference_id"])

    url = resp.get("init_point") or resp.get("sandbox_init_point")
    if not url:
        raise RuntimeError("Preference criada, mas init_point está vazio.")
    return url

def start_card_checkout(request, order_id):
    order = get_object_or_404(Order.objects.select_related("product", "customer"), pk=order_id)

    if order.payment_type != "card":
        return redirect("payment_pending", order_id=order.id)

    if order.is_expired and order.status == "pending":
        order.mark_cancelled()
        return redirect("payment_pending", order_id=order.id)

    if order.status == "paid":
        return redirect("payment_success", order_id=order.id)

    try:
        url = create_card_preference(order.product, order.customer, order, request)
    except Exception as e:
        logger.exception("Erro ao criar preference do cartão (order %s): %s", order.id, e)
        return render(request, "vendas/pending.html", {
            "order": order,
            "error": "Não foi possível iniciar o checkout do cartão. Verifique as credenciais e tente novamente."
        })
    return redirect(url)

def mp_return(request):
    payment_id = request.GET.get("payment_id") or request.GET.get("collection_id")
    preference_id = request.GET.get("preference_id")
    ext_ref = request.GET.get("external_reference") or ""

    order = None
    if ext_ref.startswith("order-"):
        try:
            oid = int(ext_ref.split("order-")[1])
            order = Order.objects.filter(pk=oid).first()
        except Exception:
            order = None
    if not order and payment_id:
        order = Order.objects.filter(payment_id=str(payment_id)).first()
    if not order and preference_id:
        order = Order.objects.filter(preference_id=str(preference_id)).order_by("-id").first()
    if not order:
        return redirect("home")

    try:
        if payment_id:
            sdk = mp_sdk()
            res = sdk.payment().get(payment_id)
            data = res.get("response", {}) or {}
            mp_status = (data.get("status") or "").lower()
            if mp_status == "approved":
                order.mark_paid()
            elif mp_status in {"rejected", "cancelled", "canceled"}:
                order.mark_cancelled()
    except Exception:
        pass

    if order.status == "paid":
        return redirect("payment_success", order_id=order.id)
    return redirect("payment_pending", order_id=order.id)

# -------------------- Pedido / Páginas principais --------------------
def get_or_reuse_pending_order(
    product: Product,
    customer: Customer,
    payment_type: str,
    shipping_address: Address | None = None,
):
    """
    Retorna (order, created).

    - Produto FÍSICO:
        * Reusa apenas se houver um pendente com o MESMO endereço.
        * Se existir pendente sem endereço e você passou um endereço agora, adota esse endereço.
        * Se já houver endereço diferente, cria um novo pedido (não sobrescreve).
    - Produto DIGITAL:
        * Ignora endereço e reusa o mais recente pendente.
    """
    base_qs = (
        Order.objects.filter(
            product=product,
            customer=customer,
            payment_type=payment_type,
            status="pending",
            expires_at__gte=timezone.now(),
        )
        .order_by("-created_at")
    )

    is_physical = getattr(product, "product_type", None) == ProductType.PHYSICAL

    if is_physical:
        # 1) Se enviaram um endereço, tente reusar exatamente com o mesmo endereço
        if shipping_address:
            same_addr = base_qs.filter(shipping_address=shipping_address).first()
            if same_addr:
                return same_addr, False

            # 2) Se há um pendente sem endereço, adota o informado agora
            no_addr = base_qs.filter(shipping_address__isnull=True).first()
            if no_addr:
                no_addr.shipping_address = shipping_address
                no_addr.save(update_fields=["shipping_address"])
                return no_addr, False

            # 3) Existe pendente, mas com outro endereço → não mexe, cria novo
            # (caindo para criação abaixo)
        else:
            # Não veio endereço ainda → reusa um pendente SEM endereço (se houver)
            no_addr = base_qs.filter(shipping_address__isnull=True).first()
            if no_addr:
                return no_addr, False

    else:
        # DIGITAL: reusa qualquer pendente
        existing = base_qs.first()
        if existing:
            # (defensivo) se por acaso tiver endereço, zera
            if existing.shipping_address_id is not None:
                existing.shipping_address = None
                existing.save(update_fields=["shipping_address"])
            return existing, False

    # Criar novo pedido
    with transaction.atomic():
        order = Order.objects.create(
            product=product,
            customer=customer,
            amount=_effective_price(product),
            status="pending",
            payment_type=payment_type,
            expires_at=timezone.now() + timedelta(days=2),
            shipping_address=shipping_address if is_physical else None,
        )
    return order, True



def home(request):
    products = Product.objects.filter(active=True).order_by("-created_at")
    now = timezone.now()
    start_30d = now - timedelta(days=30)

    paid_30d = Order.objects.filter(status="paid", created_at__gte=start_30d)
    receita_30d = paid_30d.aggregate(
        total=Coalesce(
            Sum("amount"),
            V(Decimal("0.00")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"] or Decimal("0.00")
    pedidos_30d = paid_30d.count()
    ticket_30d = (receita_30d / pedidos_30d) if pedidos_30d else Decimal("0.00")
    today_new = Product.objects.filter(active=True, created_at__date=timezone.localdate()).count()

    ctx = {
        "products": products,
        "kpi_today_new": today_new,
        "kpi_receita_30d": receita_30d,
        "kpi_pedidos_30d": pedidos_30d,
        "kpi_ticket_30d": ticket_30d,
    }
    return render(request, "vendas/home.html", ctx)

# --- checkout_view com endereço de envio para produto físico ---
def checkout_view(request, slug, token):
    product = get_object_or_404(Product, slug=slug, checkout_token=token, active=True)
    needs_shipping = (getattr(product, "product_type", None) == ProductType.PHYSICAL)

    company = (Company.objects.filter(active=True)
               .order_by("-created_at")
               .only("id", "trade_name", "corporate_name", "cnpj", "address", "phone_e164", "logo")
               .first())

    def norm_cep(v: str) -> str:
        d = re.sub(r"\D", "", v or "")
        return f"{d[:5]}-{d[5:]}" if len(d) == 8 else (v or "")

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        address_errors = []
        address_form_data = {}
        addresses_for_customer = []

        if form.is_valid():
            data = form.cleaned_data

            # --- cliente (cria/atualiza por CPF) ---
            customer, _ = Customer.objects.get_or_create(
                cpf=data["cpf"],
                defaults={
                    "full_name": data["full_name"],
                    "email": data["email"],
                    "phone": data.get("phone", ""),
                },
            )
            changed = False
            if customer.full_name != data["full_name"]:
                customer.full_name = data["full_name"]; changed = True
            if customer.email != data["email"]:
                customer.email = data["email"]; changed = True
            if data.get("phone") and customer.phone != data["phone"]:
                customer.phone = data["phone"]; changed = True
            if changed:
                customer.save()

            shipping_address = None

            # --- endereço (apenas se produto físico) ---
            if needs_shipping:
                # Endereços já salvos do cliente
                addresses_for_customer = list(
                    Address.objects.filter(customer=customer).order_by("-is_default", "-created_at")
                )

                # Descobre nomes reais dos campos do modelo
                addr_fields = {f.name for f in Address._meta.get_fields()}
                ZIP_FIELD = "zip_code" if "zip_code" in addr_fields else "cep"
                DIST_FIELD = "district" if "district" in addr_fields else "neighborhood"

                # Se escolheu um existente
                addr_id = (request.POST.get("address_id") or "").strip()
                if addr_id:
                    shipping_address = Address.objects.filter(customer=customer, pk=addr_id).first()
                    if not shipping_address:
                        address_errors.append("Endereço selecionado não foi encontrado.")
                else:
                    # Lê nomes do template (zip_code/district), com fallback para cep/neighborhood
                    raw_zip = (request.POST.get("zip_code") or request.POST.get("cep") or "").strip()
                    zip_norm = norm_cep(raw_zip)

                    label          = (request.POST.get("label") or "").strip()
                    recipient_name = (request.POST.get("recipient_name") or data.get("full_name") or "").strip()
                    street         = (request.POST.get("street") or "").strip()
                    number         = (request.POST.get("number") or "").strip()
                    complement     = (request.POST.get("complement") or "").strip()
                    district_val   = (request.POST.get("district") or request.POST.get("neighborhood") or "").strip()
                    city           = (request.POST.get("city") or "").strip()
                    state          = (request.POST.get("state") or "").strip().upper()
                    country        = (request.POST.get("country") or "Brasil").strip()

                    # Mantém para re-renderizar o template (usa as chaves do template)
                    address_form_data = {
                        "label": label,
                        "recipient_name": recipient_name,
                        "zip_code": zip_norm,
                        "street": street,
                        "number": number,
                        "complement": complement,
                        "district": district_val,
                        "city": city,
                        "state": state,
                        "country": country,
                    }

                    # validações mínimas
                    if not all([zip_norm, street, number, district_val, city, state]):
                        address_errors.append("Preencha todos os campos de endereço obrigatórios.")
                    if len(re.sub(r"\D", "", zip_norm)) != 8:
                        address_errors.append("CEP inválido (8 dígitos).")
                    if not re.fullmatch(r"[A-Z]{2}", state):
                        address_errors.append("UF inválida.")

                    # cria o endereço se válido
                    if not address_errors:
                        kwargs = {
                            "customer": customer,
                            "label": label,
                            "recipient_name": recipient_name,
                            "street": street,
                            "number": number,
                            "complement": complement,
                            "city": city,
                            "state": state,
                            "country": country,
                        }
                        kwargs[ZIP_FIELD] = zip_norm
                        kwargs[DIST_FIELD] = district_val
                        shipping_address = Address.objects.create(**kwargs)

                if not shipping_address:
                    # Reexibe com erros e mantém campos
                    others = (Product.objects.filter(active=True)
                              .exclude(pk=product.pk)
                              .order_by("-created_at")[:12])
                    ctx = {
                        "product": product,
                        "form": form,
                        "others": others,
                        "company": company,
                        "needs_shipping": True,
                        "addresses": addresses_for_customer,
                        "address_errors": address_errors,
                        "address_form_data": address_form_data,
                        "show_address_form": True,
                    }
                    return render(request, "vendas/checkout.html", ctx)

            # --- método de pagamento ---
            pay_method = (request.POST.get("pay_method") or "pix").lower().strip()
            payment_type = "card" if pay_method == "card" else "pix"

            # --- preço vigente ---
            unit_price = _effective_price(product)
            if not isinstance(unit_price, Decimal):
                unit_price = Decimal(str(unit_price))

            # --- reusar/criar pedido (considera endereço se físico) ---
            order, created = get_or_reuse_pending_order(product, customer, payment_type, shipping_address)

            if order.amount != unit_price:
                order.amount = unit_price
                order.save(update_fields=["amount"])

            _ensure_external_ref(order)

            if created:
                try:
                    send_order_created_email(order, request=request)
                except Exception as e:
                    logger.warning("Falha ao enviar e-mail de pedido criado (order %s): %s", order.id, e)

            if payment_type == "card":
                return redirect("pay_card", order_id=order.id)

            try:
                create_pix_payment(product, customer, order, request)
            except Exception as e:
                logger.exception("Erro ao criar pagamento Pix (order %s): %s", order.id, e)
                return render(request, "vendas/pending.html", {
                    "order": order,
                    "product": product,
                    "company": company,
                    "error": "Não foi possível iniciar o Pix agora. Verifique o token do MP e os logs.",
                })

            return redirect("payment_pending", order_id=order.id)

        # form inválido → reexibe
        others = (Product.objects.filter(active=True)
                  .exclude(pk=product.pk)
                  .order_by("-created_at")[:12])
        ctx = {
            "product": product,
            "form": form,
            "others": others,
            "company": company,
            "needs_shipping": needs_shipping,
            "show_address_form": needs_shipping,
            "addresses": [],
            "address_errors": [],
            "address_form_data": {},
        }
        return render(request, "vendas/checkout.html", ctx)

    # GET
    form = CheckoutForm()
    others = (Product.objects.filter(active=True)
              .exclude(pk=product.pk)
              .order_by("-created_at")[:12])
    ctx = {
        "product": product,
        "form": form,
        "others": others,
        "company": company,
        "needs_shipping": needs_shipping,
        "show_address_form": needs_shipping,
        "addresses": [],
        "address_errors": [],
        "address_form_data": {},
    }
    return render(request, "vendas/checkout.html", ctx)


def payment_pending(request, order_id):
    order = get_object_or_404(Order.objects.select_related("product", "customer", "shipping_address"), pk=order_id)

    if order.is_expired and order.status == "pending":
        order.mark_cancelled()
        order.refresh_from_db(fields=["status"])
        return render(request, "vendas/pending.html", {"order": order})

    if order.status == "pending" and order.payment_type == "pix":
        try:
            if order.payment_id:
                _refresh_pix_from_mp(order)
                if not order.pix_qr_code and not order.pix_qr_base64:
                    _refresh_pix_from_mp(order)
            else:
                create_pix_payment(order.product, order.customer, order, request)
                _refresh_pix_from_mp(order)
        except Exception as e:
            logger.exception("Falha ao garantir QR do Pix (order %s): %s", order.id, e)
            order.refresh_from_db(fields=["status", "payment_id", "pix_qr_code", "pix_qr_base64", "pix_ticket_url"])
            return render(request, "vendas/pending.html", {
                "order": order,
                "error": "Erro ao obter QR Pix. Revise o Access Token/credenciais e veja os logs."
            })

    order.refresh_from_db(fields=["status", "payment_id", "pix_qr_code", "pix_qr_base64", "pix_ticket_url"])
    return render(request, "vendas/pending.html", {"order": order})

def payment_success(request, order_id):
    order = get_object_or_404(Order.objects.select_related("product", "shipping_address"), pk=order_id)
    if order.status != "paid":
        raise Http404("Pagamento ainda não confirmado.")
    link = None
    if order.product.product_type == ProductType.DIGITAL:
        if not hasattr(order, "download_link"):
            DownloadLink.create_for_order(order)
        link = order.download_link
    return render(request, "vendas/success.html", {"order": order, "link": link, "is_digital": order.product.product_type == ProductType.DIGITAL})

# -------------------- DOWNLOAD SEGURO (Cloudinary) --------------------
def _guess_candidates(asset):
    public_id_attr = getattr(asset, "public_id", "") or ""
    name = getattr(asset, "name", "") or public_id_attr or str(asset) or ""
    base = os.path.basename(name)
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else None

    bases = {p for p in [public_id_attr, name] if p}
    pubs = []
    for pid in bases:
        pubs.append(pid)
        if pid.startswith("media/"):
            pubs.append(pid.split("media/", 1)[-1])
        else:
            pubs.append(f"media/{pid}")
        if "." in pid:
            pubs.append(pid.rsplit(".", 1)[0])

    seen = set()
    public_ids = []
    for p in pubs:
        if p and p not in seen:
            seen.add(p)
            public_ids.append(p)

    resource_types = ["raw", "image"]
    delivery_types = ["upload", "private", "authenticated"]
    formats = [ext, None]

    return public_ids, formats, resource_types, delivery_types

def _sign_url(public_id, file_format, resource_type, delivery_type, expires_at):
    try:
        if resource_type == "raw" and not file_format:
            raise ValueError("raw sem format -> pula private_download_url")
        url = private_download_url(
            public_id,
            file_format or "",
            resource_type=resource_type,
            type=delivery_type,
            expires_at=expires_at,
            attachment=True,
        )
        if url:
            return url
    except Exception as e:
        logger.debug("private_download_url falhou (%s/%s/%s.%s): %s",
                     resource_type, delivery_type, public_id, file_format or "", e)
    try:
        url, _ = cloudinary_url(
            public_id,
            resource_type=resource_type,
            type=delivery_type,
            format=file_format,
            sign_url=True,
            expires_at=expires_at,
            attachment=True,
        )
        return url
    except Exception as e:
        logger.debug("cloudinary_url falhou (%s/%s/%s.%s): %s",
                     resource_type, delivery_type, public_id, file_format or "", e)
        return None

def secure_download(request, token):
    link = get_object_or_404(DownloadLink, token=token)
    if not link.is_valid():
        raise Http404("Link inválido ou expirado.")

    asset = link.order.product.digital_file
    if not asset:
        raise Http404("Arquivo indisponível.")

    public_ids, fmts, rtypes, dtypes = _guess_candidates(asset)
    expires_at = int((timezone.now() + timedelta(minutes=3)).timestamp())

    for pid in public_ids:
        for rt in rtypes:
            for dt in dtypes:
                for fmt in fmts:
                    url = _sign_url(pid, fmt, rt, dt, expires_at)
                    if url:
                        link.download_count += 1
                        link.save(update_fields=["download_count"])
                        return HttpResponseRedirect(url)

    logger.error("Cloudinary: resource not found. Tentativas: ids=%s", public_ids)
    raise Http404("Arquivo não encontrado no Cloudinary. Reenvie o arquivo no admin (RawMediaCloudinaryStorage).")

# -------------------- Relatório --------------------
@staff_member_required
def sales_report(request):
    start = request.GET.get("start")
    end = request.GET.get("end")
    qs = Order.objects.all()
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lte=end)

    agg = qs.aggregate(
        total_vendas=Count("id"),
        total_pago=Count("id", filter=Q(status="paid")),
        receita=Sum("amount", filter=Q(status="paid")),
    )
    top = (
        qs.filter(status="paid")
        .values("product__title")
        .annotate(qtd=Count("id"), receita=Sum("amount"))
        .order_by("-receita")[:10]
    )

    return render(request, "vendas/report.html", {"agg": agg, "top": top, "start": start, "end": end})

# -------------------- Status / Polling / Webhook --------------------
def sync_payment_status_from_mp(order: Order):
    if order.status != "pending" or not order.payment_id:
        return order.status
    sdk = mp_sdk()
    result = sdk.payment().get(order.payment_id)
    data = result.get("response", {}) or {}
    status = (data.get("status") or "").lower()
    if status == "approved":
        order.mark_paid()
    elif status in {"rejected", "cancelled", "canceled"}:
        order.mark_cancelled()
    return order.status

def order_status(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if order.is_expired and order.status == "pending":
        order.mark_cancelled()
    else:
        sync_payment_status_from_mp(order)
    return JsonResponse({"status": order.status})

@csrf_exempt
def mp_webhook(request):
    try:
        body = request.body.decode("utf-8") if request.body else ""
    except Exception:
        body = ""

    payment_id = None
    if body:
        try:
            j = json.loads(body)
            payment_id = str(j.get("data", {}).get("id") or j.get("id") or "")
        except Exception:
            pass
    if not payment_id:
        payment_id = request.GET.get("id") or request.GET.get("data.id")
    if not payment_id:
        return HttpResponse("no id", status=200)

    sdk = mp_sdk()
    result = sdk.payment().get(payment_id)
    data = result.get("response", {}) or {}

    order = None
    ext = (data.get("external_reference") or "")
    if ext.startswith("order-"):
        try:
            oid = int(ext.split("order-")[1])
            order = Order.objects.filter(pk=oid).first()
        except Exception:
            order = None
    if not order:
        order = Order.objects.filter(payment_id=str(payment_id)).first()
    if not order and "preference_id" in data:
        order = Order.objects.filter(preference_id=str(data.get("preference_id"))).order_by("-id").first()
    if not order:
        return HttpResponse("order not found", status=200)

    if order.is_expired and order.status == "pending":
        order.mark_cancelled()
    else:
        status = (data.get("status") or "").lower()
        if status == "approved":
            order.mark_paid()
        elif status in {"rejected", "cancelled", "canceled"}:
            order.mark_cancelled()

    return HttpResponse("ok", status=200)

# -------------------- Catálogo público --------------------
def catalog(request):
    products = Product.objects.filter(active=True).order_by("-created_at")
    company = Company.objects.filter(active=True).first()

    now = timezone.now()
    today = timezone.localdate()
    start_30 = now - timedelta(days=30)

    today_new = Product.objects.filter(active=True, created_at__date=today).count()

    paid_30_qs = Order.objects.filter(status="paid", created_at__gte=start_30)
    agg = paid_30_qs.aggregate(
        receita_30d=Coalesce(
            Sum("amount"),
            V(Decimal("0.00")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        pedidos_30d=Count("id"),
    )
    receita_30d = agg["receita_30d"] or Decimal("0.00")
    pedidos_30d = agg["pedidos_30d"] or 0
    ticket_30d = (receita_30d / pedidos_30d) if pedidos_30d else Decimal("0.00")

    ctx = {
        "products": products,
        "company": company,
        "now": now,
        "kpi_today_new": today_new,
        "kpi_receita_30d": receita_30d,
        "kpi_pedidos_30d": pedidos_30d,
        "kpi_ticket_30d": ticket_30d,
    }
    return render(request, "vendas/home_public.html", ctx)

# -------------------- Lista de pedidos (admin simplificado) --------------------
@staff_member_required
def orders_list(request):
    qs = (
        Order.objects
        .select_related("product", "customer")
        .order_by("-created_at")
    )

    status = (request.GET.get("status") or "").lower()
    if status in {"pending", "paid", "cancelled"}:
        qs = qs.filter(status=status)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(customer__full_name__icontains=q) |
            Q(customer__email__icontains=q) |
            Q(customer__cpf__icontains=q) |
            Q(product__title__icontains=q) |
            Q(id__icontains=q)
        )

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    last_30 = timezone.now() - timedelta(days=30)
    receita_30 = (
        Order.objects.filter(status="paid", created_at__gte=last_30)
        .aggregate(total=Coalesce(Sum("amount"), V(0, output_field=DecimalField(max_digits=12, decimal_places=2))))
        .get("total") or Decimal("0")
    )
    paid_agg = (
        Order.objects.filter(status="paid")
        .aggregate(
            total=Coalesce(Sum("amount"), V(0, output_field=DecimalField(max_digits=12, decimal_places=2))),
            qtd=Coalesce(Count("id"), V(0))
        )
    )
    ticket_medio = Decimal("0")
    if paid_agg["qtd"]:
        ticket_medio = (paid_agg["total"] / Decimal(paid_agg["qtd"])).quantize(Decimal("0.01"))

    counts = {
        "todos": Order.objects.count(),
        "pending": Order.objects.filter(status="pending").count(),
        "paid": Order.objects.filter(status="paid").count(),
        "cancelled": Order.objects.filter(status="cancelled").count(),
    }

    ctx = {
        "orders": page_obj,
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "counts": counts,
        "receita_30": receita_30,
        "ticket_medio": ticket_medio,
    }
    return render(request, "vendas/orders_list.html", ctx)

@staff_member_required
@require_POST
def order_send_reminder(request, order_id):
    order = get_object_or_404(Order.objects.select_related("product", "customer"), pk=order_id)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("orders_list")

    if order.status != "pending":
        messages.info(request, f"Pedido #{order.id} não está pendente.")
        return redirect(next_url)

    try:
        ok = send_payment_reminder_email(order, request)
        if ok:
            messages.success(request, f"Lembrete enviado para {order.customer.email}.")
        else:
            messages.warning(request, "Não foi possível enviar o lembrete (status não pendente).")
    except Exception as e:
        logger.exception("Falha ao enviar lembrete (order %s): %s", order.id, e)
        messages.error(request, "Erro ao enviar e-mail de lembrete.")
    return redirect(next_url)

# -------------------- Dashboard Mobile --------------------
@login_required
def mobile_dashboard(request):
    days = int(request.GET.get("days", 7))
    tz = timezone.get_current_timezone()

    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=days-1)
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_exclusive = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)

    qs_period = Order.objects.filter(created_at__gte=start_dt, created_at__lt=end_exclusive)
    qs_paid_period = qs_period.filter(status="paid")

    kpi_period_orders = qs_period.aggregate(
        c=Coalesce(Count("id"), V(0, output_field=IntegerField()))
    )["c"] or 0
    kpi_period_revenue = qs_paid_period.aggregate(
        s=Coalesce(Sum("amount"), V(0, output_field=DecimalField(max_digits=12, decimal_places=2)))
    )["s"] or Decimal("0")
    kpi_period_ticket = (kpi_period_revenue / kpi_period_orders) if kpi_period_orders else Decimal("0")

    today_start = timezone.make_aware(datetime.combine(end_date, time.min), tz)
    tomorrow_start = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz)
    qs_today = Order.objects.filter(created_at__gte=today_start, created_at__lt=tomorrow_start)
    qs_today_paid = qs_today.filter(status="paid")

    kpi_today_orders = qs_today.aggregate(
        c=Coalesce(Count("id"), V(0, output_field=IntegerField()))
    )["c"] or 0
    kpi_today_revenue = qs_today_paid.aggregate(
        s=Coalesce(Sum("amount"), V(0, output_field=DecimalField(max_digits=12, decimal_places=2)))
    )["s"] or Decimal("0")
    kpi_today_ticket = (kpi_today_revenue / kpi_today_orders) if kpi_today_orders else Decimal("0")

    by_day_orders = (qs_period
        .annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(n=Coalesce(Count("id"), V(0, output_field=IntegerField()))))
    by_day_revenue = (qs_paid_period
        .annotate(d=TruncDate("created_at"))
        .values("d")
        .annotate(s=Coalesce(Sum("amount"), V(0, output_field=DecimalField(max_digits=12, decimal_places=2)))))

    map_cnt = {r["d"]: int(r["n"]) for r in by_day_orders}
    map_rev = {r["d"]: float(r["s"]) for r in by_day_revenue}

    labels, orders, revenue = [], [], []
    for i in range(days):
        d = start_date + timedelta(days=i)
        labels.append(d.strftime("%d/%m"))
        orders.append(map_cnt.get(d, 0))
        revenue.append(round(map_rev.get(d, 0.0), 2))

    pt_label = {"pix": "Pix", "card": "Cartão"}
    mix_qs = qs_paid_period.values("payment_type").annotate(
        s=Coalesce(Sum("amount"), V(0, output_field=DecimalField(max_digits=12, decimal_places=2)))
    )
    mix_labels = [pt_label.get(r["payment_type"], r["payment_type"]) for r in mix_qs]
    mix_values = [float(r["s"]) for r in mix_qs]

    top_qs = (qs_paid_period
        .values("product__title")
        .annotate(
            receita=Coalesce(Sum("amount"), V(0, output_field=DecimalField(max_digits=12, decimal_places=2))),
            qtd=Coalesce(Count("id"), V(0, output_field=IntegerField())),
        )
        .order_by("-receita")[:6]
    )
    top_products = [
        {"title": r["product__title"], "receita": r["receita"], "qtd": r["qtd"]}
        for r in top_qs
    ]

    ctx = {
        "period_start": start_date,
        "period_end": end_date,
        "days": days,
        "kpi_period_orders": kpi_period_orders,
        "kpi_period_revenue": kpi_period_revenue,
        "kpi_period_ticket": kpi_period_ticket,
        "kpi_today_orders": kpi_today_orders,
        "kpi_today_revenue": kpi_today_revenue,
        "kpi_today_ticket": kpi_today_ticket,
        "labels": labels,
        "revenue": revenue,
        "orders": orders,
        "mix_labels": mix_labels,
        "mix_values": mix_values,
        "top_products": top_products,
    }
    return render(request, "vendas/mobile_dashboard.html", ctx)

@login_required
@require_POST
def order_shipping_update(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    next_url = request.POST.get("next") or reverse("orders_list")

    # Só permite mudar envio se estiver pago (opcional, mas recomendado)
    if order.status != "paid":
        messages.warning(request, "Só é possível marcar envio para pedidos pagos.")
        return redirect(next_url)

    was = order.shipping_status

    shipped_flag = request.POST.get("shipped") == "on"
    code   = (request.POST.get("tracking_code") or "").strip()
    carrier = (request.POST.get("carrier") or "").strip()

    if shipped_flag:
        order.mark_shipped(tracking_code=code or order.tracking_code,
                           carrier=carrier or order.tracking_carrier,
                           save=True)
        # Se acabou de virar “Enviado”, dispara e-mail
        if was != ShippingStatus.SHIPPED:
            try:
                send_order_shipped_email(order, request=request)
                messages.success(request, f"Pedido #{order.id} marcado como Enviado e e-mail enviado ao cliente.")
            except Exception as e:
                messages.warning(request, f"Pedido #{order.id} marcado como Enviado, mas houve falha ao enviar e-mail: {e}")
    else:
        order.mark_pending_shipping(save=True)
        messages.info(request, f"Pedido #{order.id} voltou para 'Pendente de envio'.")

    # Se alterou código/transportadora sem marcar/ desmarcar, só persiste os campos:
    if not shipped_flag and (code or carrier):
        # atualiza campos sem mudar o status
        if code:
            order.tracking_code = code
        if carrier:
            order.tracking_carrier = carrier
        order.save(update_fields=["tracking_code", "tracking_carrier"])
        messages.success(request, f"Rastreio do pedido #{order.id} atualizado.")

    return redirect(next_url)

