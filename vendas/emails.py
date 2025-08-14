# vendas/emails.py
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.urls import reverse

# ------------------------
# Helpers
# ------------------------

def _abs_url(path: str, request=None) -> str:
    """
    Monta URL absoluta. Se houver request, usa request; senão, usa SITE_BASE_URL.
    """
    if request:
        return request.build_absolute_uri(path)
    base = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
    return f"{base}{path}"

def fmt_brl(value) -> str:
    """
    Formata número em BRL com separadores PT-BR.
    """
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return f"R$ {value}"

def _brand_name() -> str:
    """
    Extrai o nome da marca a partir do DEFAULT_FROM_EMAIL ou retorna um fallback.
    Ex.: "Loja Digital <email@dominio>" -> "Loja Digital"
    """
    df = getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
    if "<" in df and ">" in df:
        name = df.split("<")[0].strip()
        return name or "Loja Digital"
    return df or "Loja Digital"

def _btn(href: str, label: str, bg="#2563eb"):
    """
    Retorna HTML de botão estilizado inline (compatível com a maioria dos clientes).
    """
    return (
        f'<a href="{href}" target="_blank" '
        f'style="display:inline-block;padding:.7rem 1.1rem;'
        f'background:{bg};color:#fff;text-decoration:none;border-radius:.5rem;'
        f'font-weight:700;">{label}</a>'
    )

def _mail_wrapper(inner_html: str, preheader: str = "") -> str:
    """
    Envelope HTML padrão com tipografia e espaçamento.
    Aceita um preheader (texto curto que aparece na caixa de entrada).
    """
    pre = f'<div style="display:none;max-height:0;overflow:hidden">{preheader}</div>' if preheader else ""
    return f"""
    <div style="font-family:Inter,Segoe UI,Arial,sans-serif;line-height:1.55;color:#0f172a;background:#f6f8fb;padding:24px 0;">
      {pre}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;box-shadow:0 4px 24px rgba(2,6,23,.06);">
        <tr><td style="padding:20px 22px 6px 22px;">
          <!-- Cabeçalho simples -->
          <div style="font-weight:800;font-size:18px;color:#0f172a;">{_brand_name()}</div>
        </td></tr>
        <tr><td style="padding:8px 22px 22px 22px;">
          {inner_html}
        </td></tr>
      </table>

      <div style="max-width:640px;margin:12px auto 0 auto;text-align:center;color:#64748b;font-size:12px;">
        Este e-mail foi enviado por {_brand_name()}. Por favor, não compartilhe links pessoais de download.
      </div>
    </div>
    """


# ------------------------
# E-mails
# ------------------------

def send_order_created_email(order, request=None):
    """
    E-mail quando o pedido é criado (status 'pending').
    Mostra um link para a página 'pending' (QR Pix / instruções).
    """
    to = [getattr(order.customer, "email", None)]
    if not to[0]:
        return  # sem e-mail, não envia

    pending_path = reverse("payment_pending", args=[order.id])
    pending_url  = _abs_url(pending_path, request=request)

    brand = _brand_name()
    assunto   = f"🧾 Pedido #{order.id} recebido • {order.product.title}"
    preheader = f"Seu pedido foi criado. Valor {fmt_brl(order.amount)} — finalize o pagamento em até 2 dias."

    valor   = fmt_brl(order.amount)
    produto = order.product.title

    # Texto simples (fallback)
    text = (
        f"{preheader}\n\n"
        f"Olá {order.customer.full_name},\n\n"
        f"Recebemos seu pedido #{order.id} do produto '{produto}'.\n"
        f"Valor: {valor}\n\n"
        f"Finalize seu pagamento por aqui:\n{pending_url}\n\n"
        "Este pedido ficará pendente por 2 dias.\n\n"
        f"Qualquer dúvida, responda este e-mail.\n— {brand}\n"
    )

    # HTML
    inner = f"""
      <h2 style="margin:0 0 .25rem 0">🧾 Pedido <span style="color:#2563eb">#{order.id}</span> recebido</h2>
      <p style="margin:.25rem 0 1rem 0;color:#334155">Olá <strong>{order.customer.full_name}</strong>, recebemos seu pedido e ele está aguardando pagamento.</p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px">
        <tr>
          <td style="padding:12px 16px;font-size:14px;">
            <div><strong>Produto:</strong> {produto}</div>
            <div><strong>Valor:</strong> {valor}</div>
            <div><strong>Status:</strong> <span style="color:#dc2626">Pendente</span></div>
          </td>
        </tr>
      </table>

      <div style="margin:16px 0 6px 0">
        {_btn(pending_url, "💳 Finalizar pagamento")}
      </div>

      <p style="color:#64748b;font-size:14px;margin:.75rem 0 0 0">
        ⏳ Este pedido ficará pendente por <strong>2 dias</strong>.
      </p>

      <hr style="border:none;border-top:1px solid #e5e7eb;margin:18px 0">

      <p style="margin:.5rem 0 0 0;font-size:14px;">
        Precisa de ajuda? Responda este e-mail ou fale com nosso suporte.<br>
        Obrigado por escolher <strong>{brand}</strong>! ✨
      </p>
    """
    html = _mail_wrapper(inner, preheader=preheader)

    msg = EmailMultiAlternatives(
        subject=assunto,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to
    )
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=not getattr(settings, "DEBUG", False))


def send_order_paid_email(order, request=None):
    """
    E-mail quando o pedido muda para 'paid'. Envia o link de download seguro.
    """
    to = [getattr(order.customer, "email", None)]
    if not to[0] or not hasattr(order, "download_link"):
        return

    dl_path = reverse("secure_download", args=[order.download_link.token])
    dl_url  = _abs_url(dl_path, request=request)

    brand    = _brand_name()
    assunto  = f"✅ Pagamento confirmado • Pedido #{order.id}"
    exp_data = f"{order.download_link.expires_at:%d/%m/%Y %H:%M}"
    limite   = getattr(order.download_link, "max_downloads", 1)
    valor    = fmt_brl(order.amount)
    produto  = order.product.title

    preheader = f"Pagamento confirmado! Baixe agora seu arquivo — expira em {exp_data}."

    # Texto simples
    text = (
        f"{preheader}\n\n"
        f"Olá {order.customer.full_name},\n\n"
        f"Seu pagamento do pedido #{order.id} foi confirmado.\n"
        f"Produto: {produto}\n"
        f"Valor: {valor}\n\n"
        f"Baixe seu arquivo por aqui:\n{dl_url}\n\n"
        f"Validade: até {exp_data} (máximo de {limite} downloads).\n\n"
        "Bom proveito!\n"
        f"— {brand}\n"
    )

    # HTML
    inner = f"""
      <h2 style="margin:0 0 .25rem 0">✅ Pagamento confirmado</h2>
      <p style="margin:.25rem 0 1rem 0;color:#334155">
        Olá <strong>{order.customer.full_name}</strong>, seu pagamento do pedido
        <strong>#{order.id}</strong> foi confirmado. Obrigado pela compra! 🎉
      </p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px">
        <tr>
          <td style="padding:12px 16px;font-size:14px;">
            <div><strong>Produto:</strong> {produto}</div>
            <div><strong>Valor:</strong> {valor}</div>
            <div><strong>Status:</strong> <span style="color:#16a34a">Pago</span></div>
          </td>
        </tr>
      </table>

      <div style="margin:16px 0 6px 0">
        {_btn(dl_url, "⬇️ Baixar agora", bg="#16a34a")}
      </div>

      <p style="color:#64748b;font-size:13px;margin:.5rem 0 0 0">
        🔐 O link é pessoal e expira em <strong>{exp_data}</strong>
        (máx. <strong>{limite}</strong> downloads).
      </p>

      <hr style="border:none;border-top:1px solid #e5e7eb;margin:18px 0">

      <p style="margin:.5rem 0 0 0;font-size:14px;">
        Dúvidas com o download? Responda este e-mail que ajudamos. 😉<br>
        Aproveite seu material!<br>
        — <strong>{brand}</strong>
      </p>
    """
    html = _mail_wrapper(inner, preheader=preheader)

    msg = EmailMultiAlternatives(
        subject=assunto,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to
    )
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=not getattr(settings, "DEBUG", False))


def send_payment_reminder_email(order, request=None):
    """
    Envia lembrete de pagamento para pedidos PENDENTES.
    """
    if getattr(order, "status", "") != "pending":
        return False

    # Escolhe rota conforme método
    if getattr(order, "payment_type", "") == "pix":
        pay_path = reverse("payment_pending", args=[order.id])
    else:
        pay_path = reverse("pay_card", args=[order.id])

    pay_url   = _abs_url(pay_path, request=request)
    brand     = _brand_name()
    assunto   = f"⏰ Lembrete de pagamento • Pedido #{order.id}"
    valor     = fmt_brl(order.amount)
    produto   = order.product.title
    preheader = f"Seu pedido está pendente. Valor {valor}. Conclua o pagamento agora."

    to_email   = [getattr(order.customer, "email", None)]
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@localhost")

    # Texto simples
    body_txt = (
        f"{preheader}\n\n"
        f"Olá, {order.customer.full_name}\n\n"
        f"Seu pedido #{order.id} ({produto}) está pendente.\n"
        f"Valor: {valor}\n\n"
        f"Para concluir o pagamento, acesse:\n{pay_url}\n\n"
        f"Se já pagou, desconsidere este e-mail.\n"
        f"— {brand}\n"
    )

    # HTML
    inner = f"""
      <h2 style="margin:0 0 .25rem 0">⏰ Lembrete de pagamento</h2>
      <p style="margin:.25rem 0 1rem 0;color:#334155">
        Olá <strong>{order.customer.full_name}</strong>, identificamos que seu pedido
        <strong>#{order.id}</strong> ainda está pendente.
      </p>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px">
        <tr>
          <td style="padding:12px 16px;font-size:14px;">
            <div><strong>Produto:</strong> {produto}</div>
            <div><strong>Valor:</strong> {valor}</div>
            <div><strong>Status:</strong> <span style="color:#dc2626">Pendente</span></div>
          </td>
        </tr>
      </table>

      <div style="margin:16px 0 6px 0">
        {_btn(pay_url, "💳 Concluir pagamento")}
      </div>

      <p style="color:#64748b;font-size:13px;margin:.5rem 0 0 0">
        Se o pagamento já foi feito, pode ignorar esta mensagem. Obrigado! 🙏
      </p>

      <hr style="border:none;border-top:1px solid #e5e7eb;margin:18px 0">

      <p style="margin:.5rem 0 0 0;font-size:14px;">
        Precisa de ajuda? Responda este e-mail ou fale com nosso suporte.<br>
        — <strong>{brand}</strong>
      </p>
    """
    body_html = _mail_wrapper(inner, preheader=preheader)

    sent = send_mail(
        assunto,
        body_txt,
        from_email,
        to_email,
        html_message=body_html,
        fail_silently=not getattr(settings, "DEBUG", False),
    )
    return bool(sent)
