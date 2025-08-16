from django.urls import path
from . import views

urlpatterns = [
    # Home / catálogo
    path("", views.home, name="home"),
    path("produtos/", views.catalog, name="catalog"),

    # Checkout do produto
    path("checkout/<slug:slug>/<uuid:token>/", views.checkout_view, name="checkout"),

    # Cartão (Checkout Pro)
    path("pagamento/cartao/<int:order_id>/", views.start_card_checkout, name="pay_card"),
    path("retorno/mercadopago/", views.mp_return, name="mp_return"),

    # Pix / páginas de status
    path("pagamento/pendente/<int:order_id>/", views.payment_pending, name="payment_pending"),
    path("pagamento/sucesso/<int:order_id>/", views.payment_success, name="payment_success"),
    path("orders/<int:order_id>/status/", views.order_status, name="order_status"),

    # Webhook Mercado Pago
    path("webhooks/mercadopago/", views.mp_webhook, name="mp_webhook"),

    # Download seguro
    path("download/<uuid:token>/", views.secure_download, name="secure_download"),

    # Relatórios e lista de pedidos
    path("relatorios/vendas/", views.sales_report, name="sales_report"),
    path("pedidos/", views.orders_list, name="orders_list"),
    path("pedidos/<int:order_id>/lembrar/", views.order_send_reminder, name="order_send_reminder"),
    path("m/dashboard/", views.mobile_dashboard, name="mobile_dashboard"),

    path("orders/<int:order_id>/shipping/", views.order_shipping_update, name="order_shipping_update"),
    
]
