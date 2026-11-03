from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import Lot


class RegisterForm(UserCreationForm):
    pass


class LotForm(forms.ModelForm):
    ends_at = forms.DateTimeField(
        label="Окончание торгов",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )

    class Meta:
        model = Lot
        fields = ["title", "description", "start_price", "min_step", "ends_at"]

    def clean_ends_at(self):
        value = self.cleaned_data["ends_at"]
        if value <= timezone.now():
            raise forms.ValidationError("Время окончания должно быть в будущем.")
        return value

    def clean(self):
        data = super().clean()
        for f in ("start_price", "min_step"):
            if data.get(f) is not None and data[f] <= 0:
                self.add_error(f, "Значение должно быть больше нуля.")
        return data


class BidForm(forms.Form):
    amount = forms.DecimalField(label="Ваша ставка", max_digits=10, decimal_places=2)


class TradeForm(forms.Form):
    quantity = forms.DecimalField(label="Количество", max_digits=14, decimal_places=4,
                                  min_value=0.0001)
