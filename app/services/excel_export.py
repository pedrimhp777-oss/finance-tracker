"""Geração da planilha Excel do relatório financeiro.

A exportação deve refletir exatamente o que o usuário vê na aplicação:
mesmos filtros, mesmo fuso horário e mesma precisão monetária.
A sanitização de texto protege contra injeção de fórmulas no Excel.
"""
from datetime import timezone as dt_timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app import models


def excel_safe_text(value: str) -> str:
    """Prefixa células com potencial de injeção de fórmula."""
    first_visible = value.lstrip(" \t\r\n")[:1]
    if first_visible in {"=", "+", "-", "@"} or value[:1] in {"\t", "\r", "\n"}:
        return "'" + value
    return value


def build_workbook(
    transactions: list[models.Transaction],
    client_timezone,
) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório Financeiro"

    headers = ["Descrição", "Categoria", "Tipo", "Valor (R$)", "Data"]
    ws.append(headers)

    header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    center_align = Alignment(horizontal="center", vertical="center")

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align

    for t in transactions:
        transaction_date = t.date
        if hasattr(transaction_date, "astimezone"):
            if transaction_date.tzinfo is None:
                transaction_date = transaction_date.replace(tzinfo=dt_timezone.utc)
            transaction_date = transaction_date.astimezone(client_timezone)
        ws.append([
            excel_safe_text(t.description),
            excel_safe_text(t.category),
            "Entrada" if t.type == "income" else "Saída",
            float(t.amount),
            transaction_date.strftime("%d/%m/%Y")
            if hasattr(transaction_date, "strftime")
            else str(transaction_date),
        ])

    currency_format = "R$ #,##0.00"
    thin_border = Border(
        left=Side(style="thin", color="E0E0E0"),
        right=Side(style="thin", color="E0E0E0"),
        top=Side(style="thin", color="E0E0E0"),
        bottom=Side(style="thin", color="E0E0E0"),
    )

    for row in ws.iter_rows(
        min_row=2, max_row=len(transactions) + 1, min_col=1, max_col=5
    ):
        for cell in row:
            cell.border = thin_border
            cell.font = Font(name="Segoe UI", size=10)

        row[2].alignment = center_align
        row[4].alignment = center_align
        row[3].number_format = currency_format

    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 6, 14)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream
