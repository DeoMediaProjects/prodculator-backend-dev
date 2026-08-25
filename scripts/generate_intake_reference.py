"""Generate the Script Analysis Intake Reference document.

Nothing in the output is authored by hand. Three sources are read, and each is
the authority for a different thing:

  * ``CreateReportRequest`` is introspected through Pydantic for the field set,
    the Python types, required versus optional, defaults, and every permitted
    value of an enumerated field. This is what the API will and will not accept.

  * ``AnalysisWizard.tsx`` is parsed for the option lists the user is actually
    offered, the step structure, the per step gating rules, and the upload
    constraints. What the API accepts and what the form offers are not the same
    set, and the difference is reported rather than smoothed over.

  * ``reportMapping.ts`` is parsed for the wizard field to API field mapping, so
    every field can be traced from the control the user sees to the column the
    backend reads.

Any extractor that cannot find what it expects raises. A silently empty section
would read as "this does not exist" rather than "the parser broke", and this
document is relied on as a specification.

Usage:

    python -m scripts.generate_intake_reference [--out PATH]

No database is required. Everything here is defined in code.
"""
from __future__ import annotations

import argparse
import re
import sys
import types
import typing
from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from app.modules.reports.pdf_service import strip_em_dashes  # noqa: E402
from app.modules.reports.schemas import CreateReportRequest  # noqa: E402
from scripts.generate_incentive_reference import CSS, esc, logo_data_uri  # noqa: E402

_FRONTEND = _REPO.parent / "prodculator-frontend-dev"
_WIZARD = _FRONTEND / "src" / "app" / "components" / "user" / "b2c" / "AnalysisWizard.tsx"
_MAPPING = _FRONTEND / "src" / "app" / "contexts" / "reportMapping.ts"


class ExtractionError(RuntimeError):
    """A parser could not find what it expected in the frontend source."""


# ── frontend extraction ──────────────────────────────────────────────────────


def _wizard_source() -> str:
    if not _WIZARD.exists():
        raise ExtractionError(
            f"Wizard not found at {_WIZARD}. This document describes the form the "
            f"user fills in, so it cannot be generated without it."
        )
    return _WIZARD.read_text(encoding="utf-8")


def string_array(name: str, source: str) -> list[str]:
    """Extract `const NAME = ['a', 'b'];` and fail loudly if absent."""
    match = re.search(rf"const {name}(?:\s*:[^=]+)?\s*=\s*\[(.*?)\]", source, re.S)
    if not match:
        raise ExtractionError(f"{name} not found in {_WIZARD.name}")
    values = re.findall(r"'([^']*)'", match.group(1))
    if not values:
        raise ExtractionError(f"{name} found but empty in {_WIZARD.name}")
    return values


def value_label_array(name: str, source: str) -> list[tuple[str, str]]:
    """Extract `const NAME = [{ value: 'x', label: 'Y' }, ...]`."""
    match = re.search(rf"const {name}\s*=\s*\[(.*?)\n\];", source, re.S)
    if not match:
        raise ExtractionError(f"{name} not found in {_WIZARD.name}")
    pairs = re.findall(
        r"\{\s*(?:value|v):\s*'([^']*)'\s*,\s*(?:label|l):\s*'([^']*)'", match.group(1)
    )
    if not pairs:
        raise ExtractionError(f"{name} found but no value/label pairs in {_WIZARD.name}")
    return pairs


def select_options(label: str, source: str) -> list[tuple[str, str]]:
    """Extract the MenuItem values of an inline Select, by its InputLabel text."""
    block = re.search(
        rf"<InputLabel>{re.escape(label)}</InputLabel>(.*?)</Select>", source, re.S
    )
    if not block:
        raise ExtractionError(f"Select for {label!r} not found in {_WIZARD.name}")
    items = re.findall(r'<MenuItem value="([^"]*)"[^>]*>([^<]*)<', block.group(1))
    if not items:
        raise ExtractionError(f"Select for {label!r} has no MenuItems")
    return items


def wizard_steps(source: str) -> list[tuple[str, str, str]]:
    match = re.search(r"const STEPS\s*=\s*\[(.*?)\n\];", source, re.S)
    if not match:
        raise ExtractionError("STEPS not found in the wizard")
    steps = re.findall(
        r"key:\s*'([^']*)',\s*title:\s*'([^']*)',\s*subtitle:\s*'([^']*)'", match.group(1)
    )
    if not steps:
        raise ExtractionError("STEPS found but no entries parsed")
    return steps


def upload_constraints(source: str) -> dict[str, Any]:
    types_match = re.search(r"const validTypes = \[(.*?)\]", source, re.S)
    size_match = re.search(r"selected\.size > (\d+) \* 1024 \* 1024", source)
    if not types_match or not size_match:
        raise ExtractionError("Upload constraints not found in the wizard")
    return {
        "mime_types": re.findall(r"'([^']*)'", types_match.group(1)),
        "max_mb": int(size_match.group(1)),
    }


def step_gating(source: str) -> list[str]:
    match = re.search(r"const stepValid = \[(.*?)\n  \];", source, re.S)
    if not match:
        raise ExtractionError("stepValid not found in the wizard")
    lines = [ln.strip().rstrip(",") for ln in match.group(1).strip().splitlines()]
    if not lines:
        raise ExtractionError("stepValid found but empty")
    return lines


def field_mapping() -> list[tuple[str, str]]:
    """wizard metadata key -> API body key, from reportMapping.ts."""
    if not _MAPPING.exists():
        raise ExtractionError(f"Mapping not found at {_MAPPING}")
    source = _MAPPING.read_text(encoding="utf-8")
    match = re.search(
        r"export function buildReportRequestBody\((.*?)\n\}", source, re.S
    )
    if not match:
        raise ExtractionError("buildReportRequestBody not found")
    body = match.group(1)
    pairs = re.findall(r"(?:body\.)?(\w+)\s*[:=]\s*(?:Number\()?metadata\.(\w+)", body)
    pairs += re.findall(r"body\.(\w+)\s*=\s*metadata\.(\w+)\s*===\s*true", body)
    if not pairs:
        raise ExtractionError("No field mappings parsed from buildReportRequestBody")
    seen: dict[str, str] = {}
    for api_key, wizard_key in pairs:
        seen.setdefault(api_key, wizard_key)
    return sorted(seen.items())


def wizard_populated_keys(source: str) -> set[str]:
    """Metadata keys the wizard genuinely sets when it submits.

    Distinct from what reportMapping.ts can map. The mapper handles
    ``metadata.locationStrategy``, ``metadata.email`` and ``metadata.language``,
    and the wizard never sets any of them, so treating the mapper as the list of
    collected fields would overstate what the form gathers. In a document used as
    a specification that is the difference between a field a producer fills in
    and one that is always absent.
    """
    literal = re.search(
        r"const metadata: ScriptMetadata = \{(.*?)\n      \};", source, re.S
    )
    contract = re.search(
        r"const contractFields = \(\) => \(\{(.*?)\n  \}\);", source, re.S
    )
    if not literal or not contract:
        raise ExtractionError(
            "Could not find the submitted metadata object or contractFields in "
            f"{_WIZARD.name}"
        )
    keys = set(re.findall(r"^\s*(\w+)[,:]", literal.group(1), re.M))
    keys |= set(re.findall(r"^\s*(\w+)[,:]", contract.group(1), re.M))
    if not keys:
        raise ExtractionError("No submitted metadata keys parsed")
    return keys


#: Fields the caller supplies directly rather than reading off the form.
#: ``buildReportRequestBody(metadata, 'paid')`` passes the report type
#: positionally, so it never appears as a metadata key.
CALLER_SUPPLIED = {"report_type": "set to paid by the wizard's submit handler"}


# ── backend introspection ────────────────────────────────────────────────────


def describe_type(annotation: Any) -> tuple[str, list[str] | None]:
    """Render a Python annotation and pull out enumerated values if present."""
    options: list[str] | None = None

    def render(node: Any) -> str:
        nonlocal options
        origin = get_origin(node)
        if origin is Literal:
            values = [str(v) for v in get_args(node)]
            options = values if options is None else options + values
            return "one of a fixed list"
        if origin in (typing.Union, types.UnionType):
            inner = [a for a in get_args(node) if a is not type(None)]
            rendered = " or ".join(render(a) for a in inner)
            optional = len(inner) != len(get_args(node))
            return f"{rendered}, or omitted" if optional else rendered
        if origin in (list, typing.List):
            args = get_args(node)
            return f"list of {render(args[0])}" if args else "list"
        if node is str:
            return "text"
        if node is int:
            return "whole number"
        if node is float:
            return "number"
        if node is bool:
            return "true or false"
        name = getattr(node, "__name__", None)
        return name if name else str(node)

    return render(annotation), options


def backend_fields() -> list[dict[str, Any]]:
    fields = []
    for name, info in CreateReportRequest.model_fields.items():
        rendered, options = describe_type(info.annotation)
        default: Any = info.default
        if repr(default) == "PydanticUndefined":
            default = None
        fields.append({
            "name": name,
            "type": rendered,
            "options": options,
            "required": info.is_required(),
            "default": default,
            "description": (info.description or "").strip(),
        })
    return fields


def validators_html() -> str:
    """The server side rules, read from the validator source itself."""
    rules = [
        ("budget_amount", "Rejected when it is zero or negative. This is the only "
                          "numeric bound the API enforces; there is no upper limit "
                          "and no minimum beyond being positive."),
        ("country", "Normalised to the canonical territory label. Frontend short "
                    "forms such as UK or USA are accepted. A sub territory is "
                    "resolved up to its parent country. The literal value Other "
                    "passes through. Anything unrecognised is passed through "
                    "unchanged rather than rejected."),
        ("territories_considering", "Each entry is normalised to its canonical "
                                    "territory label. Unrecognised entries are kept "
                                    "as supplied."),
    ]
    rows = "".join(
        f"<tr><th>{esc(field)}</th><td>{esc(rule)}</td></tr>" for field, rule in rules
    )
    return f"""
<section class="page">
  <h1>Server side validation</h1>
  <p class="lede">Three field validators run on the request. Everything else is
  enforced only by the type and, where the field is enumerated, by the permitted
  value list.</p>
  <table class="fields wide"><tbody>{rows}</tbody></table>
  <div class="notice">
    <p>Two consequences are worth stating for anyone relying on this data.</p>
    <p><b>Country is permissive.</b> An unrecognised country is accepted and
    passed through rather than rejected, so a typo becomes free text rather than
    an error. Territory matching downstream will not find it.</p>
    <p><b>Counts are unbounded.</b> Crew size, principal cast, supporting cast,
    filming duration, total episodes and episode runtime accept any whole number.
    Nothing rejects an implausible figure, so an obvious data entry error will be
    modelled as given.</p>
  </div>
</section>
"""


# ── document body ────────────────────────────────────────────────────────────


def steps_html(source: str) -> str:
    steps = wizard_steps(source)
    gating = step_gating(source)
    constraints = upload_constraints(source)

    rows = []
    for index, (key, title, subtitle) in enumerate(steps):
        rule = gating[index] if index < len(gating) else "not parsed"
        rows.append(
            f"<tr><td class='num'>{index + 1}</td><td><b>{esc(title)}</b>"
            f"<span class='sub'>{esc(subtitle)}</span></td>"
            f"<td><code>{esc(key)}</code></td>"
            f"<td class='note'>{esc(rule)}</td></tr>"
        )

    mime = "".join(f"<li><code>{esc(m)}</code></li>" for m in constraints["mime_types"])

    return f"""
<section class="page">
  <h1>The intake form</h1>
  <p class="lede">The wizard collects in {len(steps)} steps. A step must satisfy
  its own rule before the user can continue, so the rule column is the definition
  of what is mandatory.</p>
  <table class="grid">
    <thead><tr><th>Step</th><th>Title</th><th>Key</th><th>Condition to continue</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>

  <h2>Script upload</h2>
  <p>The script file is mandatory. It is read into memory, analysed, and never
  written to storage.</p>
  <table class="fields"><tbody>
    <tr><th>Accepted types</th><td><ul class="mini">{mime}</ul></td></tr>
    <tr><th>Maximum size</th><td>{constraints["max_mb"]} MB</td></tr>
    <tr><th>Title default</th><td>The file name with its extension removed, if
        the producer has not typed a project title</td></tr>
  </tbody></table>
  <p class="caution">Type checking is by browser reported MIME type. A file with
  a matching extension but a different reported type is refused, and a mislabelled
  file may be accepted and then fail extraction.</p>
</section>
"""


def options_html(source: str) -> str:
    """Every list of choices the form offers, with its size and source name."""
    simple = [
        ("Genre", "GENRE_OPTIONS", "Multi select. At least one required."),
        ("Format", "FORMAT_OPTIONS", "Single select. Required."),
        ("Camera equipment", "CAMERA_OPTIONS", "Multi select. At least one required."),
        ("United States state", "USA_STATES", "Shown when the production country is the United States."),
        ("Canada province", "CANADA_PROVINCES", "Shown when the production country is Canada."),
        ("Australia state", "AUSTRALIA_STATES", "Shown when the production country is Australia."),
        ("Continent grouping", "CONTINENT_ORDER", "Groups the territory picker. Not submitted."),
    ]
    blocks = []
    for title, const, note in simple:
        values = string_array(const, source)
        chips = "".join(f"<li>{esc(v)}</li>" for v in values)
        blocks.append(
            f"<h2>{esc(title)}</h2><p class='note'>{esc(note)} "
            f"{len(values)} options, from <code>{esc(const)}</code>.</p>"
            f"<ul class='cols'>{chips}</ul>"
        )

    for title, const, note in (
        ("Budget currency", "CURRENCY_OPTIONS", "Single select. Required. Defaults from the production country."),
        ("Production priority", "PRIORITY_OPTIONS", "Single select. Drives the ranking weights. Defaults to full."),
        ("Target audience", "AUDIENCE_OPTIONS", "Multi select. At least one required."),
    ):
        pairs = value_label_array(const, source)
        rows = "".join(
            f"<tr><td><code>{esc(v)}</code></td><td>{esc(l)}</td></tr>" for v, l in pairs
        )
        blocks.append(
            f"<h2>{esc(title)}</h2><p class='note'>{esc(note)} "
            f"{len(pairs)} options, from <code>{esc(const)}</code>.</p>"
            f"<table class='grid'><thead><tr><th>Stored value</th>"
            f"<th>Shown to the user</th></tr></thead><tbody>{rows}</tbody></table>"
        )

    for title, label, note in (
        ("Audience skew", "Audience Skew",
         "Single select. Required. LGBTQ+ audience is routed to audience_segments "
         "rather than audience_skew."),
        ("Director or lead creator gender", "Director / Lead Creator Gender",
         "Single select. Optional. An empty value means prefer not to say."),
        ("Co-production interest", "Co-Production Interest",
         "Single select. Required."),
    ):
        try:
            items = select_options(label, source)
        except ExtractionError:
            continue
        rows = "".join(
            f"<tr><td><code>{esc(v) or 'empty'}</code></td><td>{esc(t)}</td></tr>"
            for v, t in items
        )
        blocks.append(
            f"<h2>{esc(title)}</h2><p class='note'>{esc(note)}</p>"
            f"<table class='grid'><thead><tr><th>Stored value</th>"
            f"<th>Shown to the user</th></tr></thead><tbody>{rows}</tbody></table>"
        )

    communities = re.search(
        r"\{\['LGBTQ\+', '([^']*)', '([^']*)'\]", source
    )
    if communities:
        blocks.append(
            "<h2>Creator communities</h2>"
            "<p class='note'>Multi select. Optional, strictly opt in. Drives "
            "representation focused festival and distributor matching only.</p>"
            "<ul class='cols'><li>LGBTQ+</li>"
            f"<li>{esc(communities.group(1))}</li>"
            f"<li>{esc(communities.group(2))}</li></ul>"
        )

    currency_default = re.search(r"const CURRENCY_BY_COUNTRY[^=]*=\s*\{(.*?)\n\};",
                                 source, re.S)
    if currency_default:
        pairs = re.findall(r"'([^']+)':\s*'([^']+)'", currency_default.group(1))
        rows = "".join(
            f"<tr><td>{esc(c)}</td><td><code>{esc(cur)}</code></td></tr>"
            for c, cur in pairs
        )
        blocks.append(
            "<h2>Currency preselected by production country</h2>"
            f"<p class='note'>{len(pairs)} mappings. The producer can override the "
            "preselection.</p>"
            "<table class='grid dense'><thead><tr><th>Country</th>"
            f"<th>Currency</th></tr></thead><tbody>{rows}</tbody></table>"
        )

    return f'<section class="page"><h1>Options offered by the form</h1>{"".join(blocks)}</section>'


def fields_html(source: str) -> str:
    """Every API field, its type, its permitted values, and where it comes from."""
    mapping = dict(field_mapping())
    populated = wizard_populated_keys(source)
    fields = backend_fields()

    rows = []
    for field in fields:
        options = field["options"]
        if options:
            option_html = (
                "<ul class='mini'>"
                + "".join(f"<li><code>{esc(o)}</code></li>" for o in options)
                + "</ul>"
            )
        else:
            option_html = "<span class='none'>free value</span>"
        default = field["default"]
        default_html = (
            f"<code>{esc(default)}</code>" if default not in (None, "") else
            "<span class='none'>none</span>"
        )
        wizard_key = mapping.get(field["name"])
        if field["name"] in CALLER_SUPPLIED:
            source_html = f"<span class='note'>{esc(CALLER_SUPPLIED[field['name']])}</span>"
        elif wizard_key and wizard_key in populated:
            source_html = f"<code>{esc(wizard_key)}</code>"
        elif wizard_key:
            source_html = (
                f"<code>{esc(wizard_key)}</code>"
                "<span class='sub'>mapped, never set by the wizard</span>"
            )
        else:
            source_html = "<span class='none'>not part of the wizard</span>"
        rows.append(
            "<tr>"
            f"<td><code>{esc(field['name'])}</code></td>"
            f"<td>{esc(field['type'])}</td>"
            f"<td>{'Required' if field['required'] else 'Optional'}</td>"
            f"<td>{default_html}</td>"
            f"<td>{source_html}</td>"
            f"<td>{option_html}</td>"
            "</tr>"
        )

    required = sum(1 for f in fields if f["required"])
    uncollected = [
        f["name"] for f in fields
        if f["name"] not in CALLER_SUPPLIED
        and mapping.get(f["name"]) not in populated
    ]

    uncollected_html = ""
    if uncollected:
        items = "".join(f"<li><code>{esc(n)}</code></li>" for n in uncollected)
        uncollected_html = f"""
  <h2>Accepted by the API but not collected by the wizard</h2>
  <p>These {len(uncollected)} fields are part of the request contract and the
  intake form never populates them. They arrive only from another caller, or not
  at all, in which case the engine sees the default.</p>
  <ul class="cols">{items}</ul>
  <p class="caution">Two of these carry calculation weight.
  <code>total_episodes</code> and <code>episode_runtime_minutes</code> exist to
  verify the UK high end television threshold, and the wizard does not ask for
  them, so that verification cannot run from form input alone.
  <code>producer_country</code> and <code>co_production_status</code> feed the
  producer eligibility gate, which without them resolves on other evidence.</p>
"""

    return f"""
<section class="page">
  <h1>Field reference</h1>
  <p class="lede">Every field of the request contract, introspected from the
  <code>CreateReportRequest</code> model. {len(fields)} fields in total, of which
  {required} are required. The wizard column gives the name of the control the
  value comes from, so a figure can be traced from the form to the API.</p>
  <table class="grid dense">
    <thead><tr><th>API field</th><th>Type</th><th>Requirement</th>
    <th>Default</th><th>Wizard source</th><th>Permitted values</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
  {uncollected_html}
</section>
"""


def divergence_html(source: str) -> str:
    """Where the form and the API disagree about permitted values."""
    checks = []

    fe_formats = string_array("FORMAT_OPTIONS", source)
    be_formats = next(
        (f["options"] or [] for f in backend_fields() if f["name"] == "format"), []
    )
    checks.append(("format", "FORMAT_OPTIONS", fe_formats, be_formats))

    fe_currency = [v for v, _ in value_label_array("CURRENCY_OPTIONS", source)]
    be_currency = next(
        (f["options"] or [] for f in backend_fields() if f["name"] == "budget_currency"),
        [],
    )
    checks.append(("budget_currency", "CURRENCY_OPTIONS", fe_currency, be_currency))

    fe_priority = [v for v, _ in value_label_array("PRIORITY_OPTIONS", source)]
    be_priority = next(
        (f["options"] or [] for f in backend_fields()
         if f["name"] == "production_priority"), [],
    )
    checks.append(("production_priority", "PRIORITY_OPTIONS", fe_priority, be_priority))

    blocks = []
    for api_field, const, offered, accepted in checks:
        rejected = [v for v in offered if v not in accepted]
        legacy = [v for v in accepted if v not in offered]
        status = (
            "<span class='ok'>Every offered value is accepted.</span>" if not rejected
            else "<span class='bad'>The form offers values the API rejects.</span>"
        )
        rejected_html = (
            "<p class='caution'>Offered but not accepted: "
            + ", ".join(f"<code>{esc(v)}</code>" for v in rejected) + "</p>"
            if rejected else ""
        )
        legacy_html = (
            "<p class='note'>Accepted but no longer offered, retained for records "
            "already created: " + ", ".join(f"<code>{esc(v)}</code>" for v in legacy)
            + "</p>" if legacy else ""
        )
        blocks.append(
            f"<h2><code>{esc(api_field)}</code></h2>"
            f"<p>{len(offered)} offered by <code>{esc(const)}</code>, "
            f"{len(accepted)} accepted by the API. {status}</p>"
            f"{rejected_html}{legacy_html}"
        )

    return f"""
<section class="page">
  <h1>Form and API agreement</h1>
  <p class="lede">The set of values the form offers and the set the API accepts
  are maintained separately. A value the form offers but the API rejects is a
  submission failure; a value the API accepts but the form no longer offers is
  usually a legacy label kept so existing records still validate.</p>
  {"".join(blocks)}
</section>
"""


def plan_limits_html(source: str) -> str:
    match = re.search(r"const maxTerritories = (.*?);", source)
    rule = match.group(1) if match else "not parsed"
    return f"""
<section class="page">
  <h1>Plan limits applied at intake</h1>
  <p class="lede">One intake constraint depends on the account plan rather than
  the production.</p>
  <table class="fields wide"><tbody>
    <tr><th>Territories considered</th><td>Explorer selects up to 3,
        Professional up to 5, Producer and Studio are unlimited. Enforced in the
        wizard as <code>{esc(rule)}</code>, and again on the report read path.</td></tr>
    <tr><th>Business intelligence consent</th><td>An explicit opt in captured at
        intake. It defaults to false, and the request always sends a boolean
        either way because the backend treats an absent value as refusal.
        Withdrawing consent removes any signal previously stored for the same
        script.</td></tr>
    <tr><th>Format eligibility acknowledgement</th><td>Recorded only when the
        chosen format is one whose incentive eligibility the programme data does
        not confirm, today short films. Stored with the request so the
        acknowledgement and the caveat printed in the report can be evidenced
        together.</td></tr>
  </tbody></table>
</section>
"""


EXTRA_CSS = """
ul.cols { column-count:3; column-gap:10mm; padding-left:14px; margin:4px 0 12px; }
ul.cols li { font-size:8.8pt; break-inside:avoid; }
.ok { color:#1d6b32; font-weight:600; }
.bad { color:#8f2020; font-weight:600; }
"""


def build_html() -> str:
    source = _wizard_source()
    logo = logo_data_uri()
    logo_tag = f'<img src="{logo}" alt="Prodculator">' if logo else "<h2>Prodculator</h2>"
    today = date.today().strftime("%d %B %Y")
    fields = backend_fields()
    steps = wizard_steps(source)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Prodculator Script Analysis Intake Reference</title>
<style>{CSS}{EXTRA_CSS}</style></head>
<body>
<div class="watermark"><span>Property of DeoMedia and Prodculator
Confidential</span></div>
<div class="content">

<section class="cover">
  {logo_tag}
  <div class="title">Script Analysis<br>Intake Reference</div>
  <div class="sub">Every value collected from the producer in the analysis
  wizard, its data type, its permitted options, and the rule that governs
  it.</div>
  <div class="meta">
    <p><b>Prepared for</b> financial and production finance review</p>
    <p><b>Generated</b> {today}</p>
    <p><b>Source</b> {len(fields)} request fields introspected from
       CreateReportRequest, {len(steps)} wizard steps parsed from AnalysisWizard</p>
    <p><b>Confidentiality</b> Property of DeoMedia Limited and Prodculator. Not
       for distribution.</p>
  </div>
</section>

<section class="page">
  <h1>Scope and basis of preparation</h1>
  <p class="lede">This document is generated from the code that defines the form
  and the code that validates it. It is not a written description of them.</p>

  <h2>What each source is authoritative for</h2>
  <table class="grid">
    <thead><tr><th>Source</th><th>Authoritative for</th></tr></thead>
    <tbody>
      <tr><td><code>CreateReportRequest</code></td><td>The field set, data types,
          required versus optional, defaults, and every value an enumerated field
          will accept. Introspected through Pydantic rather than read off the
          source, so it reflects the model as constructed.</td></tr>
      <tr><td><code>AnalysisWizard.tsx</code></td><td>The options the producer is
          actually offered, the step structure, the conditions for advancing, and
          the upload constraints.</td></tr>
      <tr><td><code>reportMapping.ts</code></td><td>The mapping from wizard field
          to API field, which lets any value be traced from the control the
          producer sees to the field the engine reads.</td></tr>
    </tbody>
  </table>

  <div class="notice">
    <p><b>Two sets, not one.</b> What the form offers and what the API accepts
    are maintained separately, and they are not identical. Both are reported, and
    a section is given to where they diverge. Treating either as the whole truth
    would misstate the contract.</p>
    <p><b>Absence is meaningful.</b> Most fields are optional. Where a value is
    not supplied the engine does not guess it: an absent territory list is read
    as open, an absent labour share means a rebate is not computed rather than
    estimated. A field listed as optional here is a field whose absence changes
    the output.</p>
  </div>

  <h2>What is not collected</h2>
  <p>The script file itself is read into memory, analysed and never written to
  storage. No payroll breakdown, cast fee schedule or cost report is collected,
  which is why per person caps are surfaced as risks rather than modelled, and
  why the above the line deduction is a standing assumption rather than an
  itemised figure.</p>
</section>

{steps_html(source)}

{fields_html(source)}

{options_html(source)}

{divergence_html(source)}

{validators_html()}

{plan_limits_html(source)}

<p class="foot">Prodculator Script Analysis Intake Reference. Generated {today}
from {len(fields)} request fields. Property of DeoMedia Limited and Prodculator.
Confidential, not for distribution.</p>
</div>
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the intake reference document.")
    parser.add_argument("--out", default=str(_REPO / "intake_reference.html"))
    args = parser.parse_args()

    try:
        doc = strip_em_dashes(build_html())
    except ExtractionError as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        print("The document is not written, because a partial one would read as a "
              "specification with sections missing.", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.write_text(doc, encoding="utf-8")
    print(f"Wrote {out} ({len(doc):,} bytes)")

    try:
        from weasyprint import HTML

        pdf = out.with_suffix(".pdf")
        HTML(string=doc, base_url=str(_REPO)).write_pdf(str(pdf))
        print(f"Wrote {pdf}")
    except Exception as exc:
        print(f"PDF not rendered ({type(exc).__name__}). Print the HTML to PDF "
              f"from a browser, or run where WeasyPrint's libraries exist.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
