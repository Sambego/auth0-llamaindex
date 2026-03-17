from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

OUTPUT_DIR = Path("paychecks")
OUTPUT_DIR.mkdir(exist_ok=True)


def money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,}"


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(lines: list[str], output_path: Path) -> None:
    content_lines = ["BT", "/F1 12 Tf"]
    y = 760
    for line in lines:
        safe_line = escape_pdf_text(line)
        content_lines.append(f"1 0 0 1 72 {y} Tm ({safe_line}) Tj")
        y -= 20
    content_lines.append("ET")
    content_stream = "\n".join(content_lines).encode("latin-1")

    objects = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    )
    objects.append(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    objects.append(
        f"5 0 obj\n<< /Length {len(content_stream)} >>\nstream\n".encode("latin-1")
        + content_stream
        + b"\nendstream\nendobj\n"
    )

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))

    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode(
            "latin-1"
        )
    )

    output_path.write_bytes(pdf)


people = [
    {
        "id": "auth0|69b1c4310b115d0768d55a95",
        "first_name": "John",
        "last_name": "Doe",
        "title": "Software Engineer",
        "gross": Decimal("5600.00"),
        "health": Decimal("185.00"),
        "retirement": Decimal("280.00"),
        "check_base": 100,
    },
    {
        "id": "auth0|69b1c4232ef057855932e20d",
        "first_name": "Jane",
        "last_name": "Smith",
        "title": "Software Engineer",
        "gross": Decimal("5750.00"),
        "health": Decimal("185.00"),
        "retirement": Decimal("290.00"),
        "check_base": 200,
    },
    {
        "id": "auth0|69b7f55de63ed0a675507d34",
        "first_name": "Mary",
        "last_name": "Johnson",
        "title": "Engineering Manager",
        "gross": Decimal("7100.00"),
        "health": Decimal("210.00"),
        "retirement": Decimal("355.00"),
        "check_base": 300,
    },
]

periods = [
    ("2026-01-05 to 2026-01-18", "2026-01-24"),
    ("2026-01-19 to 2026-02-01", "2026-02-07"),
    ("2026-02-02 to 2026-02-15", "2026-02-21"),
    ("2026-02-16 to 2026-03-01", "2026-03-07"),
]

federal_rate = Decimal("0.18")
state_rate = Decimal("0.05")
social_rate = Decimal("0.062")
medicare_rate = Decimal("0.0145")

for person in people:
    ytd_gross = Decimal("0.00")
    ytd_net = Decimal("0.00")
    first = person["first_name"].lower()

    for idx, (period, pay_date) in enumerate(periods, start=1):
        gross = person["gross"]
        federal = (gross * federal_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        state = (gross * state_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        social = (gross * social_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        medicare = (gross * medicare_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        deductions = federal + state + social + medicare + person["health"] + person["retirement"]
        net = gross - deductions

        ytd_gross += gross
        ytd_net += net

        check_number = person["check_base"] + idx

        lines = [
            "ACME COMPANY - PAYCHECK STATEMENT",
            f"Employee: {person['first_name']} {person['last_name']}",
            f"Employee ID: {person['id']}",
            f"Title: {person['title']}",
            f"Check Number: {check_number}",
            f"Pay Period: {period}",
            f"Pay Date: {pay_date}",
            "",
            f"Gross Pay: {money(gross)}",
            f"Federal Tax: {money(federal)}",
            f"State Tax: {money(state)}",
            f"Social Security: {money(social)}",
            f"Medicare: {money(medicare)}",
            f"Health Insurance: {money(person['health'])}",
            f"401(k) Contribution: {money(person['retirement'])}",
            f"Net Pay: {money(net)}",
            "",
            f"YTD Gross Pay: {money(ytd_gross)}",
            f"YTD Net Pay: {money(ytd_net)}",
        ]

        out_name = f"{first}_paycheck_{idx:02d}.pdf"
        build_pdf(lines, OUTPUT_DIR / out_name)

print(f"Generated {len(people) * len(periods)} PDFs in {OUTPUT_DIR.resolve()}")
