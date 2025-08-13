import os
import re
import json
import logging
from decimal import Decimal
from datetime import timedelta
from urllib.parse import urlparse

import mercadopago

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from cloudinary.utils import private_download_url, cloudinary_url

from .forms import CheckoutForm
from .emails import send_order_created_email, send_payment_reminder_email
from .models import Product, Customer, Order, DownloadLink, get_mp_access_token

logger = logging.getLogger(__name__)

# (Opcional) public key – não é necessária para Checkout Pro, mas mantemos fallback
try:
    from .models import get_mp_public_key
except Exception:
    def get_mp_public_key():
        return getattr(settings, "MP_PUBLIC_KEY", "")

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
    """
    Retorna URL pública/https para o webhook do MP.
    - Usa settings.MP_WEBHOOK_URL se existir.
    - Senão, monta via request e só aceita se for https e não localhost.
    - Se não houver URL pública válida, retorna "" (não envia para o MP).
    """
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
    """
    Cria pagamento Pix no MP (/v1/payments) e salva QR/código no pedido.
    Idempotente: se o pedido já tem payment_id, não cria outro.
    """
    _ensure_external_ref(order)

    if order.payment_id:  # já existe um pagamento vinculado a este pedido
        logger.info("PIX: pulando criação, order %s já possui payment_id %s", order.id, order.payment_id)
        return {"skipped": True, "reason": "already_has_payment_id"}

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
        "transaction_amount": float(product.price),
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
    """
    Reconsulta /v1/payments para preencher qr_code/qr_code_base64/ticket
    e também aplica status aprovado/cancelado se já mudou.
    """
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
    """
    Cria uma Preference (Checkout Pro) só para pagamento com CARTÃO.
    Salva preference_id no pedido e retorna a URL (init_point/sandbox_init_point).
    Idempotente: tenta reusar preference existente.
    """
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
            "unit_price": float(product.price),
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

    # Reusar preference existente se possível
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
    """
    Inicia/continua o Checkout Pro (redireciona para o init_point).
    """
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
    """
    Retorno do Checkout Pro (back_urls).
    Consulta o pagamento (se houver payment_id) e atualiza o status do pedido.
    """
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
def get_or_reuse_pending_order(product: Product, customer: Customer, payment_type: str):
    """
    Retorna (order, created). Se já existir pedido PENDENTE, não expirado,
    para o mesmo cliente+produto+método, reusa. Senão, cria um novo.
    """
    existing = (
        Order.objects
        .filter(
            product=product,
            customer=customer,
            payment_type=payment_type,
            status="pending",
            expires_at__gte=timezone.now()
        )
        .order_by("-created_at")
        .first()
    )
    if existing:
        return existing, False

    with transaction.atomic():
        order = Order.objects.create(
            product=product,
            customer=customer,
            amount=product.price,
            status="pending",
            payment_type=payment_type,
            expires_at=timezone.now() + timedelta(days=2),
        )
    return order, True

def home(request):
    products = Product.objects.filter(active=True).order_by("-created_at")

    now = timezone.now()
    start_30d = now - timedelta(days=30)

    # Apenas pedidos pagos nos últimos 30 dias
    paid_30d = Order.objects.filter(status="paid", created_at__gte=start_30d)

    receita_30d = paid_30d.aggregate(
        total=Coalesce(
            Sum("amount"),
            Value(Decimal("0.00")),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
    )["total"] or Decimal("0.00")

    pedidos_30d = paid_30d.count()
    ticket_30d = (receita_30d / pedidos_30d) if pedidos_30d else Decimal("0.00")

    today_new = Product.objects.filter(active=True, created_at__date=now.date()).count()

    ctx = {
        "products": products,
        "kpi_today_new": today_new,
        "kpi_receita_30d": receita_30d,
        "kpi_pedidos_30d": pedidos_30d,
        "kpi_ticket_30d": ticket_30d,
    }
    return render(request, "vendas/home.html", ctx)

def checkout_view(request, slug, token):
    """
    Recebe dados do cliente e, conforme o botão clicado (Pix/Cartão),
    inicia/continua o pagamento no mesmo pedido pendente.
    """
    product = get_object_or_404(Product, slug=slug, checkout_token=token, active=True)

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data

            # encontra ou cria cliente pelo CPF
            customer, _ = Customer.objects.get_or_create(
                cpf=data["cpf"],
                defaults={
                    "full_name": data["full_name"],
                    "email": data["email"],
                    "phone": data.get("phone", ""),
                },
            )
            # atualiza dados se mudaram
            changed = False
            if customer.full_name != data["full_name"]:
                customer.full_name = data["full_name"]; changed = True
            if customer.email != data["email"]:
                customer.email = data["email"]; changed = True
            if data.get("phone") and customer.phone != data["phone"]:
                customer.phone = data["phone"]; changed = True
            if changed:
                customer.save()

            # método escolhido
            pay_method = request.POST.get("pay_method", "pix")
            payment_type = "card" if pay_method == "card" else "pix"

            # Reusa pedido pendente se já houver (idempotente)
            order, created = get_or_reuse_pending_order(product, customer, payment_type)
            _ensure_external_ref(order)

            # e-mail: pedido criado (só quando criado agora)
            if created:
                try:
                    send_order_created_email(order, request=request)
                except Exception as e:
                    logger.warning("Falha ao enviar e-mail de pedido criado (order %s): %s", order.id, e)

            if payment_type == "card":
                return redirect("pay_card", order_id=order.id)

            # Pix: cria pagamento apenas se ainda não existir payment_id
            try:
                create_pix_payment(product, customer, order, request)
            except Exception as e:
                logger.exception("Erro ao criar pagamento Pix (order %s): %s", order.id, e)
                return render(request, "vendas/pending.html", {
                    "order": order,
                    "error": "Não foi possível iniciar o Pix agora. Verifique o token do MP e os logs."
                })

            return redirect("payment_pending", order_id=order.id)
    else:
        form = CheckoutForm()

    return render(request, "vendas/checkout.html", {"product": product, "form": form})

def payment_pending(request, order_id):
    """
    Garante QR/código Pix para o MESMO pedido (sem criar novo pedido).
    """
    order = get_object_or_404(Order.objects.select_related("product", "customer"), pk=order_id)

    if order.is_expired and order.status == "pending":
        order.mark_cancelled()
        order.refresh_from_db(fields=["status"])
        return render(request, "vendas/pending.html", {"order": order})

    # Garantir QR para Pix pendente
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
    order = get_object_or_404(Order, pk=order_id)
    if order.status != "paid":
        raise Http404("Pagamento ainda não confirmado.")
    if not hasattr(order, "download_link"):
        DownloadLink.create_for_order(order)
    return render(request, "vendas/success.html", {"order": order, "link": order.download_link})

# -------------------- DOWNLOAD SEGURO (Cloudinary) --------------------
# vendas/views.py (substitua apenas o secure_download e helpers)

import os
import logging
from datetime import timedelta
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from cloudinary.utils import private_download_url, cloudinary_url
from .models import DownloadLink

logger = logging.getLogger(__name__)

def _guess_candidates(asset):
    """
    Gera candidatos de public_id/format/resource_type/type.
    Cobre prefixo 'media/' (django-cloudinary-storage) e extensão no nome.
    """
    public_id_attr = getattr(asset, "public_id", "") or ""
    name = getattr(asset, "name", "") or public_id_attr or str(asset) or ""

    # extrai extensão do nome do arquivo (se houver)
    base = os.path.basename(name)
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else None

    # candidatos de public_id
    bases = {p for p in [public_id_attr, name] if p}
    pubs = []
    for pid in bases:
        # original
        pubs.append(pid)
        # sem 'media/'
        if pid.startswith("media/"):
            pubs.append(pid.split("media/", 1)[-1])
        else:
            # com 'media/'
            pubs.append(f"media/{pid}")
        # sem extensão
        if "." in pid:
            pubs.append(pid.rsplit(".", 1)[0])

    # dedup
    seen = set()
    public_ids = []
    for p in pubs:
        if p and p not in seen:
            seen.add(p)
            public_ids.append(p)

    # tipos possíveis
    resource_types = ["raw", "image"]
    delivery_types = ["upload", "private", "authenticated"]
    formats = [ext, None]  # tenta com a extensão e sem

    return public_ids, formats, resource_types, delivery_types


def _sign_url(public_id, file_format, resource_type, delivery_type, expires_at):
    """
    Tenta 1) private_download_url (preferível p/ private/raw)
          2) cloudinary_url assinado
    """
    # 1) private_download_url
    try:
        # Para 'raw', o Cloudinary geralmente espera o public_id SEM extensão
        # e o 'format' separado. Se não houver formato, pule esse método.
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

    # 2) cloudinary_url assinado
    try:
        url, _ = cloudinary_url(
            public_id,
            resource_type=resource_type,
            type=delivery_type,
            format=file_format,          # ok ser None
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

    # tenta várias combinações até achar uma válida
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
    """
    Webhook do Mercado Pago: identifica o payment_id, consulta /v1/payments e
    aplica o status ao pedido (paid/cancelled).
    """
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

    # encontra o pedido
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

    # aplica status
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

    # KPIs
    today = timezone.localdate()
    start_30 = timezone.now() - timedelta(days=30)

    today_new = Product.objects.filter(active=True, created_at__date=today).count()

    paid_30_qs = Order.objects.filter(status="paid", created_at__gte=start_30)
    agg = paid_30_qs.aggregate(
        receita_30d=Sum("amount"),
        pedidos_30d=Count("id"),
    )
    receita_30d = agg["receita_30d"] or 0
    pedidos_30d = agg["pedidos_30d"] or 0
    ticket_30d = (receita_30d / pedidos_30d) if pedidos_30d else 0

    ctx = {
        "products": products,
        "kpi_today_new": today_new,
        "kpi_receita_30d": receita_30d,
        "kpi_pedidos_30d": pedidos_30d,
        "kpi_ticket_30d": ticket_30d,
    }
    return render(request, "vendas/home_public.html", ctx)

# -------------------- Lista de pedidos (admin simplificado) --------------------
@staff_member_required
def orders_list(request):
    """
    Lista de pedidos com busca/filtro/paginação e KPIs.
    """
    qs = (
        Order.objects
        .select_related("product", "customer")
        .order_by("-created_at")
    )

    # Filtros
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

    # Paginação
    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    # KPIs (receita últimos 30d e ticket médio de todos pagos)
    last_30 = timezone.now() - timedelta(days=30)
    receita_30 = (
        Order.objects.filter(status="paid", created_at__gte=last_30)
        .aggregate(total=Coalesce(Sum("amount"), Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))))
        .get("total") or Decimal("0")
    )
    paid_agg = (
        Order.objects.filter(status="paid")
        .aggregate(
            total=Coalesce(Sum("amount"), Value(0, output_field=DecimalField(max_digits=12, decimal_places=2))),
            qtd=Coalesce(Count("id"), Value(0))
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
