import json
from datetime import datetime
from pathlib import Path


def save_json(data: dict, path: str) -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[output] Saved JSON   → {out.resolve()}")
    return str(out.resolve())


def save_report(data: dict, path: str) -> str:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_build_report(data))
    print(f"[output] Saved Report → {out.resolve()}")
    return str(out.resolve())


def _build_report(data: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("CLINIC SCRAPER REPORT")
    lines.append(f"Generated : {datetime.now().strftime('%B %d, %Y %I:%M %p')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("CLINIC INFORMATION")
    lines.append("-" * 40)
    lines.append(f"Name    : {data.get('clinic_name') or 'N/A'}")
    lines.append(f"Address : {data.get('address') or 'N/A'}")
    lines.append(f"Phone   : {data.get('phone') or 'N/A'}")
    lines.append(f"Email   : {data.get('email') or 'N/A'}")
    lines.append(f"Website : {data.get('website') or data.get('_source_url') or 'N/A'}")
    lines.append("")

    providers = data.get("providers", [])
    lines.append(f"PROVIDERS  ({len(providers)} found)")
    lines.append("-" * 40)

    if not providers:
        lines.append("No providers found.")
    else:
        for i, p in enumerate(providers, 1):
            in_state = p.get("licensed_in_target_state")
            badge = "[LICENSED]" if in_state is True else ("[NOT VERIFIED]" if in_state is False else "")
            lines.append(f"{i}. {p.get('name', 'Unknown')}  {badge}")
            if p.get("npi"):
                lines.append(f"   NPI             : {p['npi']}")
            if p.get("title"):
                lines.append(f"   Title           : {p['title']}")
            if p.get("specialty"):
                lines.append(f"   Specialty       : {p['specialty']}")
            if p.get("current_employer"):
                lines.append(f"   Current Employer: {p['current_employer']}")
            if p.get("license_number"):
                lines.append(f"   License No.     : {p['license_number']}")
            if p.get("license_states"):
                states = ", ".join(p["license_states"]) if isinstance(p["license_states"], list) else p["license_states"]
                lines.append(f"   Licensed In     : {states}")
            if p.get("phone"):
                lines.append(f"   Phone           : {p['phone']}")
            if p.get("email"):
                lines.append(f"   Email           : {p['email']}")
            lines.append("")

    lines.append("-" * 40)
    usage = data.get("_usage", {})
    source = data.get("_source_url") or data.get("_source") or "N/A"
    lines.append(f"Source  : {source}")
    if usage:
        lines.append(f"Tokens  : {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out")
    fda = data.get("fda_registration", {})
    if fda:
        lines.append(f"FDA Reg : {'Registered' if fda.get('fda_registered') else 'Not found in FDA CDRH database'}")
    lines.append("=" * 60)

    return "\n".join(lines)
