
from django import forms
import re


from django import forms

class CheckoutForm(forms.Form):
    full_name = forms.CharField(
        label="Nome completo",
        max_length=160,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "name",
            "placeholder": "Seu nome completo",
            "autofocus": "autofocus",
        })
    )
    cpf = forms.CharField(
        label="CPF",
        max_length=14,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "inputmode": "numeric",
            "placeholder": "000.000.000-00",
            "data-mask": "cpf",
        })
    )
    phone = forms.CharField(
        label="Telefone/WhatsApp",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "inputmode": "tel",
            "placeholder": "(00) 00000-0000",
            "data-mask": "phone",
        })
    )
    email = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(attrs={
            "class": "form-control",
            "autocomplete": "email",
            "placeholder": "seu@email.com",
        })
    )


