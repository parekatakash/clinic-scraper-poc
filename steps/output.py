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
            if p.get("provider_category"):
                lines.append(f"   Provider Type   : {p['provider_category']}")
            if p.get("npi"):
                lines.append(f"   NPI             : {p['npi']}")
            if p.get("title"):
                lines.append(f"   Title           : {p['title']}")
            if p.get("specialty"):
                lines.append(f"   Specialty       : {p['specialty']}")
            if p.get("current_employer"):
                lines.append(f"   Current Employer: {p['current_employer']}")
            # State medical license
            if p.get("license_number"):
                lines.append(f"   License No.     : {p['license_number']}")
            if p.get("license_states"):
                states = ", ".join(p["license_states"]) if isinstance(p["license_states"], list) else p["license_states"]
                lines.append(f"   Licensed In     : {states}")
            if p.get("license_status"):
                lines.append(f"   License Status  : {p['license_status']}")
            if p.get("board_actions"):
                lines.append(f"   Board Actions   : {'; '.join(p['board_actions'])}")
            if p.get("state_board_url"):
                lines.append(f"   State Board URL : {p['state_board_url']}")
            # DEA registration
            dea = p.get("dea", {})
            if dea:
                dea_num = dea.get("dea_number")
                if dea_num:
                    valid = dea.get("dea_valid_checksum")
                    dea_type = dea.get("dea_registrant_type", "")
                    status = "Valid checksum" if valid else "Invalid checksum"
                    lines.append(f"   DEA Number      : {dea_num} ({status}){' — ' + dea_type if dea_type else ''}")
                    if dea.get("dea_schedules"):
                        lines.append(f"   DEA Schedules   : {dea['dea_schedules']}")
                else:
                    lines.append(f"   DEA             : Not retrieved — verify at {dea.get('dea_lookup_url', 'deadiversion.usdoj.gov')}")
            elif p.get("requires_dea"):
                lines.append(f"   DEA             : Verify at https://www.deadiversion.usdoj.gov/webforms/deaRegistrationSearch.jsp")
            # Veterinary license
            vet = p.get("veterinary_license", {})
            if vet:
                found = vet.get("vet_license_found")
                if found is True:
                    lines.append(f"   Vet License     : Found via AAVSB VetVerify")
                elif found is False:
                    lines.append(f"   Vet License     : Not found in AAVSB VetVerify")
                else:
                    lines.append(f"   Vet License     : Verify manually (AAVSB blocks automation)")
                lines.append(f"   VetVerify URL   : {vet.get('vetverify_lookup_url', '')}")
                if vet.get("state_vet_board_url"):
                    lines.append(f"   State Vet Board : {vet['state_vet_board_url']}")
            # Pharmacy license
            if p.get("pharmacy_license_url"):
                lines.append(f"   Pharmacy Lic.   : Verify at {p['pharmacy_license_url']}")
            # Medicare enrollment
            med = p.get("medicare_enrollment", {})
            if med:
                enrolled = med.get("medicare_enrolled")
                if enrolled is True:
                    lines.append(f"   Medicare        : Enrolled ({med.get('medicare_provider_type', '')})")
                elif enrolled is False:
                    lines.append(f"   Medicare        : Not enrolled")
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
