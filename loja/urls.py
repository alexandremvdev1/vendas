# loja/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),

    # App principal
    path('', include('vendas.urls')),

    # Autenticação
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='registration/login.html',
            redirect_authenticated_user=True,   # se já logado, manda para LOGIN_REDIRECT_URL
        ),
        name='login',
    ),
    path(
        'logout/',
        auth_views.LogoutView.as_view(
            next_page=settings.LOGOUT_REDIRECT_URL  # garante redirecionamento após sair
        ),
        name='logout',
    ),
]

# Desenvolvimento: servir mídia/estático
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Se quiser também servir estáticos sem collectstatic:
    # from django.conf import settings as dj_settings
    # urlpatterns += static(settings.STATIC_URL, document_root=getattr(dj_settings, "STATIC_ROOT", None))
