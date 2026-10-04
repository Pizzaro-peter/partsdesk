from django import forms
from django.db.models import Q

from catalog.models import Part
from core.models import User
from sales.models import Customer, CustomerVehicle, Sale

from .models import JobCard


def _mechanics(tenant, branch):
    return User.objects.filter(tenant=tenant, is_active=True, role__in=["mechanic", "manager", "owner"]).filter(
        Q(role="owner") | Q(branches=branch)
    ).distinct().order_by("first_name", "username")


class JobCreateForm(forms.ModelForm):
    customer = forms.ModelChoiceField(Customer.objects.filter(is_active=True), widget=forms.HiddenInput)
    vehicle = forms.ModelChoiceField(CustomerVehicle.objects.none())

    class Meta:
        model = JobCard
        fields = ["customer", "vehicle", "complaint", "odometer", "assigned_to", "promised_date"]
        widgets = {"complaint": forms.Textarea(attrs={"rows": 3}),
                   "promised_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop("tenant")
        branch = kwargs.pop("branch")
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.filter(tenant=tenant, is_active=True)
        self.fields["assigned_to"].queryset = _mechanics(tenant, branch)
        cid = (self.data or self.initial).get("customer")
        if str(cid or "").isdigit():
            self.fields["vehicle"].queryset = CustomerVehicle.objects.filter(customer_id=cid, customer__tenant=tenant)

    def clean(self):
        cd = super().clean()
        if cd.get("customer") and cd.get("vehicle") and cd["vehicle"].customer_id != cd["customer"].pk:
            self.add_error("vehicle", "That vehicle doesn't belong to this customer.")
        return cd


class JobUpdateForm(forms.ModelForm):
    class Meta:
        model = JobCard
        fields = ["status", "assigned_to", "promised_date", "complaint", "diagnosis"]
        widgets = {"complaint": forms.Textarea(attrs={"rows": 2}), "diagnosis": forms.Textarea(attrs={"rows": 3}),
                   "promised_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop("tenant")
        branch = kwargs.pop("branch")
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = _mechanics(tenant, branch)
        self.fields["status"].choices = [c for c in JobCard.Status.choices if c[0] not in ("invoiced", "cancelled")]


class JobPartForm(forms.Form):
    part = forms.ModelChoiceField(Part.objects.filter(is_active=True), widget=forms.HiddenInput)
    quantity = forms.DecimalField(min_value=0.01, decimal_places=2, initial=1)

    def __init__(self, *a, tenant, branch=None, **kw):
        super().__init__(*a, **kw)
        self.fields["part"].queryset = Part.objects.filter(tenant=tenant, is_active=True)


class JobLabourForm(forms.Form):
    description = forms.CharField(max_length=200)
    hours = forms.DecimalField(min_value=0.01, decimal_places=2)
    rate = forms.DecimalField(min_value=0, decimal_places=2)


class JobInvoiceForm(forms.Form):
    payment_method = forms.ChoiceField(choices=Sale.Method.choices)
    amount_paid = forms.DecimalField(required=False, min_value=0, decimal_places=2,
                                     help_text="Blank = paid in full. Use 'On account' for credit customers.")
