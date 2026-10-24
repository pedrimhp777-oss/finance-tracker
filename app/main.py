import os
from datetime import timezone
from io import BytesIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from scalar_fastapi import get_scalar_api_reference
from dotenv import load_dotenv
from sqlalchemy import extract, func, text
from sqlalchemy.orm import Session
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.database import engine, Base, get_db
from app import models
from app.routes import transactions, auth
from app.security import get_current_user

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Finance Tracker API", docs_url=None, redoc_url=None)

def excel_safe_text(value: str) -> str:
    first_visible = value.lstrip(" \t\r\n")[:1]
    if first_visible in {"=", "+", "-", "@"} or value[:1] in {"\t", "\r", "\n"}:
        return "'" + value
    return value


# Declaração explícita das origens para permitir envio de tokens/credenciais
app_env = os.getenv("APP_ENV", "development").lower()
frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
origins = [frontend_url] if frontend_url else []
if app_env in {"development", "dev", "local"}:
    origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in origins if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(transactions.router)


@app.get("/", include_in_schema=False)
def home():
    return {"message": "API de Finanças rodando com sucesso!"}


@app.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}

@app.get("/docs", include_in_schema=False)
def scalar_html():
    return get_scalar_api_reference(
        openapi_url=app.openapi_url,
        title=app.title,
    )

@app.get("/transactions/export/excel")
def export_excel(
    month: int | None = Query(None, ge=1, le=12),
    year: int | None = Query(None, ge=1, le=9999),
    category: str | None = Query(None, min_length=1, max_length=100),
    search: str | None = Query(None, max_length=240),
    timezone_name: str = Query("UTC", max_length=64),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        client_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="Fuso horário inválido")

    query = db.query(models.Transaction).filter(models.Transaction.user_id == current_user.id)
    if db.get_bind().dialect.name == "postgresql":
        # Convert stored instants to the browser timezone before applying month/year filters.
        local_date = func.timezone(timezone_name, models.Transaction.date)
        if month is not None:
            query = query.filter(extract("month", local_date) == month)
        if year is not None:
            query = query.filter(extract("year", local_date) == year)
    if category:
        query = query.filter(models.Transaction.category == category)
    if search:
        query = query.filter(models.Transaction.description.ilike(f"%{search.strip()}%"))
    user_transactions = query.order_by(models.Transaction.date.desc()).all()
    if db.get_bind().dialect.name != "postgresql" and (month is not None or year is not None):
        def in_selected_period(transaction):
            value = transaction.date
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            local_value = value.astimezone(client_timezone)
            return (month is None or local_value.month == month) and (year is None or local_value.year == year)
        user_transactions = [transaction for transaction in user_transactions if in_selected_period(transaction)]

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

    for t in user_transactions:
        transaction_date = t.date
        if hasattr(transaction_date, "astimezone"):
            if transaction_date.tzinfo is None:
                transaction_date = transaction_date.replace(tzinfo=timezone.utc)
            transaction_date = transaction_date.astimezone(client_timezone)
        ws.append([
            excel_safe_text(t.description),
            excel_safe_text(t.category),
            "Entrada" if t.type == "income" else "Saída",
            float(t.amount),
            transaction_date.strftime("%d/%m/%Y") if hasattr(transaction_date, 'strftime') else str(transaction_date)
        ])

    currency_format = 'R$ #,##0.00'
    thin_border = Border(
        left=Side(style='thin', color='E0E0E0'),
        right=Side(style='thin', color='E0E0E0'),
        top=Side(style='thin', color='E0E0E0'),
        bottom=Side(style='thin', color='E0E0E0')
    )

    for row in ws.iter_rows(min_row=2, max_row=len(user_transactions) + 1, min_col=1, max_col=5):
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
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 6, 14)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)

    filename = "relatorio_financeiro.xlsx"
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

