from django.urls import path
from . import views
from . import views as v

urlpatterns = [
    path("", views.home, name="home"),

    path("checkout/<slug:slug>/<uuid:token>/", views.checkout_view, name="checkout"),
    path("pagamento/pendente/<int:order_id>/", views.payment_pending, name="payment_pending"),
    path("pagamento/sucesso/<int:order_id>/", views.payment_success, name="payment_success"),
    path("download/<uuid:token>/", views.secure_download, name="secure_download"),
    path("relatorios/vendas/", views.sales_report, name="sales_report"),

    # Pix
    path("orders/<int:order_id>/status/", views.order_status, name="order_status"),
    path("webhooks/mercadopago/", views.mp_webhook, name="mp_webhook"),
    path('produtos/', v.catalog, name='catalog'),
    path("pedidos/", v.orders_list, name="orders_list"),
    path("pedidos/<int:order_id>/lembrar/", v.order_send_reminder, name="order_send_reminder"),

]
