"""Application board CRUD. HTTP/MCP must call these same functions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hunt.core.errors import NotFoundError, ValidationError
from hunt.core.events import append_event
from hunt.core.ids import new_id, now_iso
from hunt.core.pay import DerivedPay, QuotedPay, derive_pay
from hunt.core.workspace import Workspace

APPLICATION_STATUSES = (
    "researching",
    "prepared",
    "sent",
    "waiting",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "parked",
)
MODALITIES = ("remote", "hybrid", "onsite")
ENGAGEMENTS = ("b2b", "fte", "uop", "unknown")

UNSET = object()


@dataclass
class Application:
    id: str
    company: str
    source: str | None
    url: str | None
    title_posted: str | None
    title_ours: str | None
    location_country: str | None
    location_city: str | None
    modality: str | None
    office_days_per_week: float | None
    engagement: str | None
    duration_months: int | None
    comp_quoted: QuotedPay | None
    comp_notes: str | None
    tax_home_for_net: str | None
    languages_required: list[str]
    recruiter: str | None
    cv_variant_id: str | None
    knockouts: list[str]
    extra: dict[str, Any]
    status: str
    comp_derived: DerivedPay | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "company": self.company,
            "source": self.source,
            "url": self.url,
            "title_posted": self.title_posted,
            "title_ours": self.title_ours,
            "location_country": self.location_country,
            "location_city": self.location_city,
            "modality": self.modality,
            "office_days_per_week": self.office_days_per_week,
            "engagement": self.engagement,
            "duration_months": self.duration_months,
            "comp_quoted": self.comp_quoted.to_dict() if self.comp_quoted else None,
            "comp_notes": self.comp_notes,
            "tax_home_for_net": self.tax_home_for_net,
            "languages_required": list(self.languages_required),
            "recruiter": self.recruiter,
            "cv_variant_id": self.cv_variant_id,
            "knockouts": list(self.knockouts),
            "extra": dict(self.extra),
            "status": self.status,
            "comp_derived": self.comp_derived.to_dict() if self.comp_derived else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValidationError("languages/knockouts must be a JSON list")
            return [str(v) for v in parsed]
        return [part.strip() for part in text.split(",") if part.strip()]
    raise ValidationError("expected a list")


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValidationError("extra must be a JSON object")
        return parsed
    raise ValidationError("extra must be a JSON object")


def _quoted(
    amount: float | None,
    currency: str | None,
    unit: str | None,
) -> QuotedPay | None:
    present = [amount is not None, bool(currency), bool(unit)]
    if not any(present):
        return None
    if not all(present):
        raise ValidationError(
            "quoted pay requires --comp-amount, --comp-currency, and --comp-unit"
        )
    try:
        return QuotedPay(amount=float(amount), currency=str(currency), unit=str(unit))
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _validate_status(status: str) -> str:
    if status not in APPLICATION_STATUSES:
        raise ValidationError(
            f"status must be one of {list(APPLICATION_STATUSES)}, got {status!r}"
        )
    return status


def _validate_modality(modality: str | None) -> str | None:
    if modality is None or modality == "":
        return None
    if modality not in MODALITIES:
        raise ValidationError(
            f"modality must be one of {list(MODALITIES)}, got {modality!r}"
        )
    return modality


def _validate_engagement(engagement: str | None) -> str | None:
    if engagement is None or engagement == "":
        return None
    if engagement not in ENGAGEMENTS:
        raise ValidationError(
            f"engagement must be one of {list(ENGAGEMENTS)}, got {engagement!r}"
        )
    return engagement


def derive_for_workspace(
    ws: Workspace,
    quoted: QuotedPay | None,
    tax_home: str | None,
) -> DerivedPay | None:
    return derive_pay(
        quoted,
        display_currency=ws.display_currency,
        fx_as_of=ws.fx_as_of,
        fx_rates=ws.fx_rates,
        hours_per_month=ws.hours_per_month,
        tax_home=tax_home,
        tax_homes=ws.tax_homes,
        comp_floor=ws.comp_floor,
    )


def _row_to_application(row) -> Application:
    quoted = None
    if row["comp_amount"] is not None:
        quoted = QuotedPay(
            amount=row["comp_amount"],
            currency=row["comp_currency"],
            unit=row["comp_unit"],
        )
    derived = None
    if row["fx_as_of"] and row["derived_hour"] is not None:
        clears = row["derived_clears_floor"]
        derived = DerivedPay(
            display_currency=row["display_currency"] or "",
            fx_as_of=row["fx_as_of"],
            hour=row["derived_hour"],
            day=row["derived_day"],
            month=row["derived_month"],
            year=row["derived_year"],
            net_month=row["derived_net_month"],
            clears_floor=None if clears is None else bool(clears),
        )
    return Application(
        id=row["id"],
        company=row["company"],
        source=row["source"],
        url=row["url"],
        title_posted=row["title_posted"],
        title_ours=row["title_ours"],
        location_country=row["location_country"],
        location_city=row["location_city"],
        modality=row["modality"],
        office_days_per_week=row["office_days_per_week"],
        engagement=row["engagement"],
        duration_months=row["duration_months"],
        comp_quoted=quoted,
        comp_notes=row["comp_notes"],
        tax_home_for_net=row["tax_home_for_net"],
        languages_required=json.loads(row["languages_required"] or "[]"),
        recruiter=row["recruiter"],
        cv_variant_id=row["cv_variant_id"],
        knockouts=json.loads(row["knockouts"] or "[]"),
        extra=json.loads(row["extra"] or "{}"),
        status=row["status"],
        comp_derived=derived,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_SELECT = "SELECT * FROM applications"


def list_applications(
    ws: Workspace, *, status: str | None = None
) -> list[Application]:
    if status:
        _validate_status(status)
        rows = ws.conn.execute(
            _SELECT + " WHERE status = ? ORDER BY updated_at DESC, id DESC",
            (status,),
        ).fetchall()
    else:
        rows = ws.conn.execute(
            _SELECT + " ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    return [_row_to_application(r) for r in rows]


def get_application(ws: Workspace, application_id: str) -> Application:
    row = ws.conn.execute(
        _SELECT + " WHERE id = ?", (application_id,)
    ).fetchone()
    if not row:
        raise NotFoundError(f"application not found: {application_id}")
    return _row_to_application(row)


def _derived_columns(derived: DerivedPay | None) -> dict[str, Any]:
    if derived is None:
        return {
            "fx_as_of": None,
            "display_currency": None,
            "derived_hour": None,
            "derived_day": None,
            "derived_month": None,
            "derived_year": None,
            "derived_net_month": None,
            "derived_clears_floor": None,
        }
    clears = derived.clears_floor
    return {
        "fx_as_of": derived.fx_as_of,
        "display_currency": derived.display_currency,
        "derived_hour": derived.hour,
        "derived_day": derived.day,
        "derived_month": derived.month,
        "derived_year": derived.year,
        "derived_net_month": derived.net_month,
        "derived_clears_floor": None if clears is None else int(clears),
    }


def create_application(
    ws: Workspace,
    *,
    company: str,
    source: str | None = None,
    url: str | None = None,
    title_posted: str | None = None,
    title_ours: str | None = None,
    location_country: str | None = None,
    location_city: str | None = None,
    modality: str | None = None,
    office_days_per_week: float | None = None,
    engagement: str | None = None,
    duration_months: int | None = None,
    comp_amount: float | None = None,
    comp_currency: str | None = None,
    comp_unit: str | None = None,
    comp_notes: str | None = None,
    tax_home_for_net: str | None = None,
    languages_required: list[str] | str | None = None,
    recruiter: str | None = None,
    cv_variant_id: str | None = None,
    knockouts: list[str] | str | None = None,
    extra: dict[str, Any] | str | None = None,
    status: str = "researching",
    application_id: str | None = None,
    event_kind: str = "created",
    event_body: str | None = None,
    commit: bool = True,
) -> Application:
    company = (company or "").strip()
    if not company:
        raise ValidationError("company is required")
    status = _validate_status(status)
    modality = _validate_modality(modality)
    engagement = _validate_engagement(engagement)
    quoted = _quoted(comp_amount, comp_currency, comp_unit)
    derived = derive_for_workspace(ws, quoted, tax_home_for_net)
    now = now_iso()
    app_id = application_id or new_id()
    langs = _json_list(languages_required)
    knocks = _json_list(knockouts)
    extra_obj = _json_object(extra)
    cols = _derived_columns(derived)
    ws.conn.execute(
        """
        INSERT INTO applications(
            id, company, source, url, title_posted, title_ours,
            location_country, location_city, modality, office_days_per_week,
            engagement, duration_months, comp_amount, comp_currency, comp_unit,
            comp_notes, tax_home_for_net, languages_required, recruiter,
            cv_variant_id, knockouts, extra, status,
            fx_as_of, display_currency, derived_hour, derived_day,
            derived_month, derived_year, derived_net_month, derived_clears_floor,
            created_at, updated_at
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            app_id,
            company,
            source,
            url or None,
            title_posted,
            title_ours,
            location_country,
            location_city,
            modality,
            office_days_per_week,
            engagement,
            duration_months,
            quoted.amount if quoted else None,
            quoted.currency if quoted else None,
            quoted.unit if quoted else None,
            comp_notes,
            tax_home_for_net,
            json.dumps(langs),
            recruiter,
            cv_variant_id,
            json.dumps(knocks),
            json.dumps(extra_obj),
            status,
            cols["fx_as_of"],
            cols["display_currency"],
            cols["derived_hour"],
            cols["derived_day"],
            cols["derived_month"],
            cols["derived_year"],
            cols["derived_net_month"],
            cols["derived_clears_floor"],
            now,
            now,
        ),
    )
    append_event(ws, app_id, event_kind, event_body or company)
    if commit:
        ws.conn.commit()
    (ws.root / "attachments" / "applications" / app_id).mkdir(
        parents=True, exist_ok=True
    )
    return get_application(ws, app_id)


def update_application(ws: Workspace, application_id: str, **fields: Any) -> Application:
    current = get_application(ws, application_id)
    data = current.to_dict()
    allowed = {
        "company",
        "source",
        "url",
        "title_posted",
        "title_ours",
        "location_country",
        "location_city",
        "modality",
        "office_days_per_week",
        "engagement",
        "duration_months",
        "comp_amount",
        "comp_currency",
        "comp_unit",
        "comp_notes",
        "tax_home_for_net",
        "languages_required",
        "recruiter",
        "cv_variant_id",
        "knockouts",
        "extra",
        "status",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValidationError(f"unknown application fields: {sorted(unknown)}")

    changed: dict[str, Any] = {}
    for key, value in fields.items():
        if value is UNSET:
            continue
        changed[key] = value

    if not changed:
        return current

    if "company" in changed:
        company = (changed["company"] or "").strip()
        if not company:
            raise ValidationError("company is required")
        data["company"] = company
    for key in (
        "source",
        "url",
        "title_posted",
        "title_ours",
        "location_country",
        "location_city",
        "comp_notes",
        "tax_home_for_net",
        "recruiter",
        "cv_variant_id",
    ):
        if key in changed:
            val = changed[key]
            data[key] = None if val == "" else val
    if "status" in changed:
        data["status"] = _validate_status(changed["status"])
    if "modality" in changed:
        data["modality"] = _validate_modality(changed["modality"] or None)
    if "engagement" in changed:
        data["engagement"] = _validate_engagement(changed["engagement"] or None)
    if "office_days_per_week" in changed:
        data["office_days_per_week"] = changed["office_days_per_week"]
    if "duration_months" in changed:
        data["duration_months"] = changed["duration_months"]
    if "languages_required" in changed:
        data["languages_required"] = _json_list(changed["languages_required"])
    if "knockouts" in changed:
        data["knockouts"] = _json_list(changed["knockouts"])
    if "extra" in changed:
        data["extra"] = _json_object(changed["extra"])

    quoted_dict = data["comp_quoted"] or {}
    amount = quoted_dict.get("amount")
    currency = quoted_dict.get("currency")
    unit = quoted_dict.get("unit")
    if "comp_amount" in changed:
        amount = changed["comp_amount"]
    if "comp_currency" in changed:
        currency = changed["comp_currency"] or None
    if "comp_unit" in changed:
        unit = changed["comp_unit"] or None
    quoted = _quoted(amount, currency, unit)
    derived = derive_for_workspace(ws, quoted, data.get("tax_home_for_net"))
    cols = _derived_columns(derived)
    now = now_iso()
    ws.conn.execute(
        """
        UPDATE applications SET
            company=?, source=?, url=?, title_posted=?, title_ours=?,
            location_country=?, location_city=?, modality=?, office_days_per_week=?,
            engagement=?, duration_months=?, comp_amount=?, comp_currency=?,
            comp_unit=?, comp_notes=?, tax_home_for_net=?, languages_required=?,
            recruiter=?, cv_variant_id=?, knockouts=?, extra=?, status=?,
            fx_as_of=?, display_currency=?, derived_hour=?, derived_day=?,
            derived_month=?, derived_year=?, derived_net_month=?,
            derived_clears_floor=?, updated_at=?
        WHERE id=?
        """,
        (
            data["company"],
            data["source"],
            data["url"],
            data["title_posted"],
            data["title_ours"],
            data["location_country"],
            data["location_city"],
            data["modality"],
            data["office_days_per_week"],
            data["engagement"],
            data["duration_months"],
            quoted.amount if quoted else None,
            quoted.currency if quoted else None,
            quoted.unit if quoted else None,
            data["comp_notes"],
            data["tax_home_for_net"],
            json.dumps(data["languages_required"]),
            data["recruiter"],
            data["cv_variant_id"],
            json.dumps(data["knockouts"]),
            json.dumps(data["extra"]),
            data["status"],
            cols["fx_as_of"],
            cols["display_currency"],
            cols["derived_hour"],
            cols["derived_day"],
            cols["derived_month"],
            cols["derived_year"],
            cols["derived_net_month"],
            cols["derived_clears_floor"],
            now,
            application_id,
        ),
    )
    append_event(
        ws,
        application_id,
        "updated",
        json.dumps(sorted(changed.keys())),
    )
    ws.conn.commit()
    return get_application(ws, application_id)
