from django.urls import path
from . import views

app_name = "precificacao"

urlpatterns = [
    path("", views.painel, name="dashboard"),  # painel inicial
    path("materias/novo/", views.materia_prima_create, name="materia_prima_create"),
    path("produtos/novo/", views.produto_create, name="produto_create"),
    path("produtos/<int:pk>/", views.produto_detail, name="produto_detail"),
    path("orcamentos/novo/", views.orcamento_create, name="orcamento_create"),
    path("orcamentos/<int:pk>/", views.orcamento_detail, name="orcamento_detail"),
    path("orcamentos/<int:pk>/imprimir/", views.orcamento_print, name="orcamento_print"),

]
