# vendas/emails.py
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse

def _abs_url(path: str, request=None) -> str:
    """
    Monta URL absoluta. Se houver request, usa request; senão, usa SITE_BASE_URL.
    """
    if request:
        return request.build_absolute_uri(path)
    base = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
    return f"{base}{path}"

def send_order_created_email(order, request=None):
    """
    Email quando o pedido é criado (status 'pending').
    Mostra um link para a página 'pending' (onde aparece o QR Pix e o polling).
    """
    to = [order.customer.email]
    if not to[0]:
        return  # sem e-mail, não envia

    pending_path = reverse("payment_pending", args=[order.id])
    pending_url  = _abs_url(pending_path, request=request)

    subject = f"Pedido #{order.id} recebido"
    text = (
        f"Olá {order.customer.full_name},\n\n"
        f"Recebemos seu pedido #{order.id} do produto '{order.product.title}'.\n"
        f"Valor: R$ {order.amount}\n\n"
        f"Para concluir o pagamento, acesse:\n{pending_url}\n\n"
        f"Este pedido ficará pendente por 2 dias.\n\n"
        "Obrigado!"
    )
    html = f"""
    <p>Olá <strong>{order.customer.full_name}</strong>,</p>
    <p>Recebemos seu pedido <strong>#{order.id}</strong> do produto <em>{order.product.title}</em>.<br>
    <strong>Valor:</strong> R$ {order.amount}</p>
    <p>Para concluir o pagamento, acesse:<br>
    <a href="{pending_url}" target="_blank">{pending_url}</a></p>
    <p>Este pedido ficará pendente por <strong>2 dias</strong>.</p>
    <p>Obrigado!</p>
    """

    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, to)
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=True)

def send_order_paid_email(order, request=None):
    """
    Email quando o pedido muda para 'paid'. Envia o link de download seguro.
    """
    to = [order.customer.email]
    if not to[0] or not hasattr(order, "download_link"):
        return

    dl_path = reverse("secure_download", args=[order.download_link.token])
    dl_url  = _abs_url(dl_path, request=request)

    subject = f"Pagamento confirmado • Pedido #{order.id}"
    text = (
        f"Olá {order.customer.full_name},\n\n"
        f"Seu pagamento do pedido #{order.id} foi confirmado.\n"
        f"Produto: {order.product.title}\n"
        f"Valor: R$ {order.amount}\n\n"
        f"Baixe seu arquivo por aqui:\n{dl_url}\n\n"
        f"Validade: até {order.download_link.expires_at:%d/%m/%Y %H:%M} "
        f"(máximo de {order.download_link.max_downloads} downloads).\n\n"
        "Bom proveito!"
    )
    html = f"""
    <p>Olá <strong>{order.customer.full_name}</strong>,</p>
    <p>Seu pagamento do pedido <strong>#{order.id}</strong> foi confirmado.<br>
    <strong>Produto:</strong> {order.product.title}<br>
    <strong>Valor:</strong> R$ {order.amount}</p>
    <p><a href="{dl_url}" target="_blank" style="display:inline-block;padding:.6rem 1rem;background:#16a34a;color:#fff;text-decoration:none;border-radius:.5rem">Baixar agora</a></p>
    <p><small>O link é pessoal e expira em {order.download_link.expires_at:%d/%m/%Y %H:%M} (máx. {order.download_link.max_downloads} downloads).</small></p>
    <p>Bom proveito!</p>
    """

    msg = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, to)
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=True)

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

def _abs(request, path: str) -> str:
    if request:
        return request.build_absolute_uri(path)
    base = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
    return f"{base}{path}"

def send_payment_reminder_email(order, request=None):
    """
    Envia lembrete de pagamento para pedidos PENDENTES.
    """
    if order.status != "pending":
        return False

    if order.payment_type == "pix":
        pay_path = reverse("payment_pending", args=[order.id])
    else:
        pay_path = reverse("pay_card", args=[order.id])

    pay_url = _abs(request, pay_path)

    subject = f"Lembrete de pagamento — {order.product.title}"
    to_email = [order.customer.email]
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@localhost")

    body_txt = (
        f"Olá, {order.customer.full_name}\n\n"
        f"Seu pedido #{order.id} ({order.product.title}) está pendente.\n"
        f"Valor: R$ {order.amount}\n\n"
        f"Para concluir o pagamento, acesse:\n{pay_url}\n\n"
        f"Se já pagou, desconsidere este e-mail.\n"
        f"— YARIN IMPRESSÕES"
    )

    body_html = f"""
    <p>Olá, {order.customer.full_name}</p>
    <p>Seu pedido <strong>#{order.id}</strong> (<em>{order.product.title}</em>) está <strong>pendente</strong>.</p>
    <p><strong>Valor:</strong> R$ {order.amount}</p>
    <p>
      Para concluir o pagamento, clique em:
      <br><a href="{pay_url}">{pay_url}</a>
    </p>
    <p style="color:#64748b">Se já pagou, pode ignorar este e-mail.</p>
    <p>— YARIN IMPRESSÕES</p>
    """

    send_mail(subject, body_txt, from_email, to_email, html_message=body_html)
    return True
