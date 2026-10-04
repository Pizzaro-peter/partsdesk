"""Job card rules. Parts are issued (stock deducted) the moment they're added to a job,
so the shelf count is always honest; invoicing later must not deduct them again."""
from core.audit import audit_actor
from django.db import transaction
from decimal import Decimal

from core.models import Sequence, audit
from core.tenancy import check_staff_branch, same_tenant
from sales.services import M, SaleError, create_sale, sale_number
from stock.services import record_movement

from .models import JobCard, JobLabour, JobPart

S = JobCard.Status


def _check_open(job):
    if job.is_locked:
        raise SaleError(f"{job.number} is {job.get_status_display().lower()} and can't be changed.")


@audit_actor
@transaction.atomic
def create_job(*, user, branch, customer, vehicle, complaint, odometer=None, assigned_to=None, promised_date=None):
    same_tenant(branch.tenant, customer)
    check_staff_branch(user, branch)
    if vehicle.customer_id != customer.pk:
        raise SaleError("Invalid customer or vehicle for this branch.")
    if assigned_to and (assigned_to.tenant_id != branch.tenant_id or
                        (assigned_to.role != "owner" and not assigned_to.branches.filter(pk=branch.pk).exists())):
        raise SaleError("Mechanic is not assigned to this branch.")
    return JobCard.objects.create(
        branch=branch, number=Sequence.next(branch, "job", "JOB"), customer=customer, vehicle=vehicle, complaint=complaint,
        odometer=odometer, assigned_to=assigned_to, promised_date=promised_date, created_by=user)


@audit_actor
@transaction.atomic
def add_part(job, part, qty, user):
    check_staff_branch(user, job.branch)
    same_tenant(job.branch.tenant, part)
    job = JobCard.objects.select_for_update().get(pk=job.pk)
    _check_open(job)
    qty = sale_number(qty, label="Quantity", minimum=Decimal("0.01"))
    record_movement(part, -qty, "job_use", user, reference=job.number, branch=job.branch)
    row = JobPart.objects.create(job=job, part=part, quantity=qty, added_by=user,
                                 unit_price=part.price_for(job.customer.is_trade))
    return row


@audit_actor
@transaction.atomic
def remove_part(job_part, user):
    check_staff_branch(user, job_part.job.branch)
    job = JobCard.objects.select_for_update().get(pk=job_part.job_id)
    _check_open(job)
    # A request can have loaded this row before another request removed it.
    # Re-read it only after acquiring the job lock; never restore stale data.
    job_part = JobPart.objects.select_for_update().select_related("part").filter(
        pk=job_part.pk, job=job).first()
    if job_part is None:
        raise SaleError("That part has already been removed from this job.")
    record_movement(job_part.part, job_part.quantity, "job_return", user, reference=job.number, branch=job.branch)
    job_part.delete()


@audit_actor
def add_labour(job, description, hours, rate):
    _check_open(job)
    hours = sale_number(hours, label="Hours", minimum=Decimal("0.01"))
    rate = sale_number(rate, label="Labour rate", minimum=0)
    return JobLabour.objects.create(job=job, description=description, hours=hours, rate=rate)


@audit_actor
def remove_labour(labour):
    _check_open(labour.job)
    labour.delete()


@audit_actor
@transaction.atomic
def cancel_job(job, user):
    check_staff_branch(user, job.branch)
    job = JobCard.objects.select_for_update().get(pk=job.pk)
    _check_open(job)
    for jp in job.parts.select_related("part"):
        record_movement(jp.part, jp.quantity, "job_return", user, reference=job.number, note="Job cancelled", branch=job.branch)
    job.status = S.CANCELLED
    job.save(update_fields=["status", "updated_at"])
    audit(user, "job.cancel", job.number, branch=job.branch)


@audit_actor
@transaction.atomic
def invoice_job(job, user, payment_method=M.CASH, amount_paid=None):
    check_staff_branch(user, job.branch)
    job = JobCard.objects.select_for_update().get(pk=job.pk)
    _check_open(job)
    lines = [dict(part=p.part, description=p.part.name, quantity=p.quantity, unit_price=p.unit_price,
                  deduct=False) for p in job.parts.select_related("part")]
    lines += [dict(part=None, description=f"Labour: {l.description}", quantity=l.hours, unit_price=l.rate,
                   is_labour=True) for l in job.labour.all()]
    sale = create_sale(user=user, branch=job.branch, lines=lines, customer=job.customer, vehicle=job.vehicle,
                       payment_method=payment_method, amount_paid=amount_paid, notes=f"Job card {job.number}")
    job.invoice, job.status = sale, S.INVOICED
    job.save(update_fields=["invoice", "status", "updated_at"])
    return sale
