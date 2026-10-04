"""Stable parent links for authenticated application pages."""
from django.urls import reverse

# Use known GET destinations rather than browser history or an incoming referrer.
PARENTS = {
    'part_detail': ('part_list', 'Back to parts'),
    'part_create': ('part_list', 'Back to parts'),
    'category_list': ('part_list', 'Back to parts'),
    'vehicle_list': ('part_list', 'Back to parts'),
    'supplier_list': ('part_list', 'Back to parts'),
    'catalogue_upload': ('part_list', 'Back to parts'),
    'catalogue_preview': ('catalogue_upload', 'Back to catalogue upload'),
    'supplier_create': ('supplier_list', 'Back to suppliers'),
    'supplier_edit': ('supplier_list', 'Back to suppliers'),
    'vehicle_edit': ('vehicle_list', 'Back to vehicles'),
    'customer_detail': ('customer_list', 'Back to customers'),
    'customer_create': ('customer_list', 'Back to customers'),
    'sale_detail': ('sale_list', 'Back to invoices'),
    'job_detail': ('job_list', 'Back to job cards'),
    'job_create': ('job_list', 'Back to job cards'),
    'po_detail': ('po_list', 'Back to purchase orders'),
    'po_create': ('po_list', 'Back to purchase orders'),
    'stock_adjust': ('movement_list', 'Back to stock ledger'),
    'report_sales': ('reports_home', 'Back to reports'),
    'report_top': ('reports_home', 'Back to reports'),
    'report_low_stock': ('reports_home', 'Back to reports'),
    'report_slow': ('reports_home', 'Back to reports'),
    'report_stock_value': ('reports_home', 'Back to reports'),
    'report_receivables': ('reports_home', 'Back to reports'),
}
RECORD_PARENTS = {
    'part_edit': ('part_detail', 'Back to part'),
    'customer_edit': ('customer_detail', 'Back to customer'),
    'sale_return': ('sale_detail', 'Back to invoice'),
}


def page_back_link(request):
    if request is None or not request.user.is_authenticated:
        return None
    match = request.resolver_match
    name = match.url_name if match else None
    if name == 'dashboard':
        return None
    kwargs = {}
    if name == 'vehicle_create':
        # Catalogue vehicle applications and customer vehicles share this name.
        if 'pk' in match.kwargs:
            target, label = 'customer_detail', 'Back to customer'
            kwargs = {'pk': match.kwargs['pk']}
        else:
            target, label = 'vehicle_list', 'Back to vehicles'
    elif name in RECORD_PARENTS:
        target, label = RECORD_PARENTS[name]
        kwargs = {'pk': match.kwargs['pk']}
    else:
        if name not in PARENTS:
            return None
        target, label = PARENTS[name]
    return {'url': reverse(target, kwargs=kwargs), 'label': label}
