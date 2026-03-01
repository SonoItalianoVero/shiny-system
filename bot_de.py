# -*- coding: utf-8 -*-

from __future__ import annotations

import io, os, re, logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal

from weasyprint import HTML
from jinja2 import Environment, FileSystemLoader
from dateutil.relativedelta import relativedelta
from datetime import date

from PIL import Image as PILImage
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing, Rect, Circle

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

# ---- logging
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("higobi-de")

# ---- reportlab
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    Image, KeepTogether
)
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = Path(__file__).resolve().parent

# ---------- TIME ----------
TZ_DE = ZoneInfo("Europe/Berlin")
def now_de_date() -> str:
    return datetime.now(TZ_DE).strftime("%d.%m.%Y")

# ---------- FONTS ----------
try:
    pdfmetrics.registerFont(TTFont("PTMono", "fonts/PTMono-Regular.ttf"))
    pdfmetrics.registerFont(TTFont("PTMono-Bold", "fonts/PTMono-Bold.ttf"))
    F_MONO = "PTMono"; F_MONO_B = "PTMono-Bold"
except Exception:
    F_MONO = "Courier"; F_MONO_B = "Courier-Bold"

# ---------- COMPANY / CONSTANTS ----------
COMPANY = {
    "brand": "GafnerImmo",
    "legal": "GafnerImmo Credit UG",
    "addr":  "Johann-Georg-Schlosser-Straße 11, 76149 Karlsruhe, Deutschland",
    "reg":   "Handelsregister: HRB 755353; Stammkapital: 25.002,00 EUR",
    "rep":   "",
    "contact": "Telegram @GafnerImmo",
    "email": "info@gafner-immo.de",
    "web": "gafner-immo.de",
    "business_scope": (
        "Die Verwaltung von Grundbesitz aller Art einschließlich der Tätigkeit als Verwalter nach § 26a WEG "
        "sowie die Mietverwaltung, die Erstellung von Betriebskostenabrechnungen, der Kauf, Verkauf, die Vermietung, "
        "Entwicklung, Beratung und Projektierung von Immobilien und Grundstücken aller Art (Makler und "
        "Darlehensvermittler i.S. des § 34c Abs. 1 Satz 1 Nr. 1 und 2 GewO), die Immobiliardarlehensvermittlung "
        "i.S. des § 34i GewO, die Erstellung von Immobiliengutachten, die Entrümpелung, die Tatortreinigung."
    ),
}

SEPA = {"ci": "DE98ZZZ00123950001", "prenotice_days": 7}

# ---------- BANK PROFILES ----------
BANKS = {
    "DE": {"name": "Santander Consumer Bank AG", "addr": "Budapester Str. 37, 10787 Berlin"},
    "AT": {"name": "Santander Consumer Bank GmbH", "addr": "Wagramer Straße 19, 1220 Wien"},
}
def get_bank_profile(cc: str) -> dict:
    return BANKS.get(cc.upper(), BANKS["DE"])

def asset_path(*candidates: str) -> str:
    """Ищем ассет: рядом с модулем, затем CWD, затем ASSETS_DIR, затем /mnt/data."""
    roots = [BASE_DIR / "assets", BASE_DIR, Path.cwd() / "assets", Path.cwd()]
    env_dir = os.getenv("ASSETS_DIR")
    if env_dir:
        roots.insert(0, Path(env_dir))
    roots.append(Path("/mnt/data"))

    for name in candidates:
        for root in roots:
            p = (root / name).resolve()
            if p.exists():
                return str(p)

    log.warning("ASSET NOT FOUND, tried: %s", ", ".join(candidates))
    return str((BASE_DIR / "assets" / candidates[0]).resolve())

# ---------- ASSETS ----------
ASSETS = {
    "logo_partner1": asset_path("santander1.png", "SANTANDER1.PNG"),
    "logo_partner2": asset_path("santander2.png", "SANTANDER2.PNG"),
    "logo_santa":    asset_path("santa.png", "SANTA.PNG", "santander1.png", "SANTANDER1.PNG"),
    "logo_higobi":   asset_path("HIGOBI_LOGO.PNG", "HIGOBI_LOGO.png",
                                "higobi_logo.png", "higobi_logo.PNG", "HIGOBI_logo.png"),
    "sign_bank":     asset_path("wagnersign.png", "wagnersign.PNG"),
    "sign_c2g":      asset_path("duraksign.png", "duraksign.PNG"),
    "stamp_santa":   asset_path("santastamp.png", "SANTASTAMP.PNG"),
    "sign_kirk":     asset_path("kirk.png", "KIRK.PNG"),
    "exclam":        asset_path("exclam.png", "exclam.PNG"),
    "notary_pdf":    asset_path("notary_template.pdf", "Notarielle Beglaubigung des Kreditvertrags #2690497-7.pdf"),
}

# ---------- UI ----------
BTN_AML      = "Письмо АМЛ/комплаенс"
BTN_CARD     = "Выдача на карту"
BTN_BOTH     = "Контракт + SEPA"
BTN_NOTARY   = "Редактировать нотариальное заверение (PDF)"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_AML),  KeyboardButton(BTN_CARD)],
        [KeyboardButton(BTN_BOTH), KeyboardButton(BTN_NOTARY)],
    ],
    resize_keyboard=True,
)

# ---------- HELPERS ----------
def fmt_eur(v: float | Decimal) -> str:
    if isinstance(v, Decimal):
        v = float(v)
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"

def fmt_eur_de_no_cents(v):
    if isinstance(v, Decimal): v = float(v)
    s = f"{v:,.0f}".replace(",", "X").replace(".", ".").replace("X", ".")
    return f"{s} €"

def fmt_eur_de_with_cents(v):
    if isinstance(v, Decimal): v = float(v)
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"

def parse_num(txt: str) -> float:
    t = txt.strip().replace(" ", "").replace(".", "").replace(",", ".")
    return float(t)

def parse_money(txt: str) -> Decimal:
    t = (txt or "").strip().upper()
    t = t.replace("€", "").replace("EUR", "").replace(" ", "")
    t = t.replace(".", "").replace(",", ".")
    if not re.match(r"^-?\d+(\.\d+)?$", t):
        raise ValueError("bad money")
    return Decimal(t)

def monthly_payment(principal: float, tan_percent: float, months: int) -> float:
    if months <= 0:
        return 0.0
    r = (tan_percent / 100.0) / 12.0
    if r == 0:
        return principal / months
    return principal * (r / (1 - (1 + r) ** (-months)))

def img_box(path: str, max_h: float, max_w: float | None = None) -> Image | None:
    if not os.path.exists(path):
        log.warning("IMAGE NOT FOUND: %s", os.path.abspath(path))
        return None
    try:
        ir = ImageReader(path); iw, ih = ir.getSize()
        scale_h = max_h / float(ih)
        scale_w = (max_w / float(iw)) if max_w else scale_h
        scale = min(scale_h, scale_w)
        return Image(path, width=iw * scale, height=ih * scale)
    except Exception as e:
        log.error("IMAGE LOAD ERROR %s: %s", path, e)
        return None

def logo_flatten_trim(path: str, max_h: float, max_w: float | None = None) -> Image | None:
    if not os.path.exists(path):
        log.warning("IMAGE NOT FOUND: %s", path)
        return None
    try:
        im = PILImage.open(path).convert("RGBA")
        alpha = im.split()[-1]
        bbox = alpha.getbbox()
        if bbox:
            im = im.crop(bbox)
            alpha = im.split()[-1]
        bg = PILImage.new("RGB", im.size, "#FFFFFF")
        bg.paste(im, mask=alpha)
        bio = io.BytesIO()
        bg.save(bio, format="PNG", optimize=True)
        bio.seek(0)
        ir = ImageReader(bio)
        iw, ih = ir.getSize()
        scale_h = max_h / float(ih)
        scale_w = (max_w / float(iw)) if max_w else scale_h
        scale = min(scale_h, scale_w)
        return Image(bio, width=iw * scale, height=ih * scale)
    except Exception as e:
        log.error("LOGO CLEAN ERROR %s: %s", path, e)
        return None

def logo_img_smart(path: str, max_h: float, max_w: float | None = None):
    im = logo_flatten_trim(path, max_h, max_w)
    if not im:
        try:
            ir = ImageReader(path)
            iw, ih = ir.getSize()
            scale_h = max_h / float(ih)
            scale_w = (max_w / float(iw)) if max_w else scale_h
            scale = min(scale_h, scale_w)
            im = Image(path, width=iw * scale, height=ih * scale)
        except Exception as e:
            log.error("FALLBACK IMAGE LOAD ERROR %s: %s", path, e)
            return Spacer(1, max_h)
    return im

def logos_header_weighted(row_width: float, h_center: float = 26*mm, side_ratio: float = 0.82) -> Table:
    col = row_width / 3.0
    h_side = h_center * side_ratio
    left   = logo_img_smart(ASSETS["logo_higobi"],   h_side,  col*0.95)
    center = logo_img_smart(ASSETS["logo_partner1"], h_center, col*0.95)
    right  = logo_img_smart(ASSETS["logo_partner2"], h_side,  col*0.95)
    t = Table([[left, center, right]], colWidths=[col, col, col], hAlign="CENTER")
    t.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN",(0,0),(0,0),"LEFT"),
        ("ALIGN",(1,0),(1,0),"CENTER"),
        ("ALIGN",(2,0),(2,0),"RIGHT"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0),  ("BOTTOMPADDING",(0,0),(-1,-1),0),
    ]))
    return t

def exclam_flowable(h_px: float = 28) -> renderPDF.GraphicsFlowable:
    h = float(h_px); w = h * 0.42
    d = Drawing(w, h)
    bar_w = w * 0.36; bar_h = h * 0.68; bar_x = (w - bar_w) / 2.0; bar_y = h * 0.20
    d.add(Rect(bar_x, bar_y, bar_w, bar_h, rx=bar_w * 0.25, ry=bar_w * 0.25,
               fillColor=colors.HexColor("#D73737"), strokeWidth=0))
    r = w * 0.18
    d.add(Circle(w / 2.0, h * 0.10, r, fillColor=colors.HexColor("#D73737"), strokeWidth=0))
    return renderPDF.GraphicsFlowable(d)

def draw_border_and_pagenum(canv, doc):
    w, h = A4
    canv.saveState()
    m = 10 * mm; inner = 6
    canv.setStrokeColor(colors.HexColor("#0E2A47")); canv.setLineWidth(2)
    canv.rect(m, m, w - 2*m, h - 2*m, stroke=1, fill=0)
    canv.rect(m+inner, m+inner, w - 2*(m+inner), h - 2*(m+inner), stroke=1, fill=0)
    canv.setFont(F_MONO, 9); canv.setFillColor(colors.black)
    canv.drawCentredString(w/2.0, 5*mm, str(canv.getPageNumber()))
    canv.restoreState()

# ---------- STATES ----------
ASK_COUNTRY = 10
ASK_CLIENT, ASK_AMOUNT, ASK_TAN, ASK_EFF, ASK_TERM = range(20, 25)
ASK_FEE = 25
(SDD_NAME, SDD_ADDR, SDD_CITY, SDD_COUNTRY, SDD_ID, SDD_IBAN, SDD_BIC) = range(100, 107)  # SDD_NAME больше не используется в «both»
(AML_NAME, AML_ID, AML_IBAN) = range(200, 203)
(CARD_NAME, CARD_ADDR) = range(300, 302)
ASK_NOTARY_AMOUNT = 410

# ---------- CONTRACT PDF ----------
# ================== НОВАЯ МАТЕМАТИКА ==================
def calculate_amortization_schedule(principal: float, tan_percent: float, months: int):
    """Возвращает аннуитет, сумму процентов и массив для Tilgungsplan"""
    if months <= 0 or principal <= 0:
        return 0, 0, []

    monthly_rate = (tan_percent / 100.0) / 12.0
    if monthly_rate == 0:
        annuity = principal / months
    else:
        annuity = principal * (monthly_rate / (1 - (1 + monthly_rate) ** (-months)))

    schedule = []
    remaining_balance = principal
    total_interest_paid = 0.0
    current_date = date.today() + relativedelta(months=1)

    for i in range(1, months + 1):
        if i == months:
            interest_part = remaining_balance * monthly_rate
            principal_part = remaining_balance
            annuity = principal_part + interest_part
        else:
            interest_part = remaining_balance * monthly_rate
            principal_part = annuity - interest_part

        interest_rounded = round(interest_part, 2)
        principal_rounded = round(principal_part, 2)
        annuity_rounded = round(interest_rounded + principal_rounded, 2)

        remaining_balance -= principal_rounded
        if remaining_balance < 0.01: remaining_balance = 0.0

        total_interest_paid += interest_rounded

        schedule.append({
            "nr": i,
            "date": current_date.strftime("%d.%m.%Y"),
            "payment": fmt_eur_de_with_cents(annuity_rounded),
            "interest": fmt_eur_de_with_cents(interest_rounded),
            "principal": fmt_eur_de_with_cents(principal_rounded),
            "balance": fmt_eur_de_with_cents(remaining_balance)
        })
        current_date += relativedelta(months=1)

    return round(annuity, 2), round(total_interest_paid, 2), schedule


# ================== НОВЫЙ ГЕНЕРАТОР КОНТРАКТА ==================
def build_contract_pdf(values: dict) -> bytes:
    # 1. Забираем данные из FSM
    client = (values.get("client", "") or "").strip()
    amount = float(values.get("amount", 0) or 0)
    tan = float(values.get("tan", 0) or 0)
    eff = float(values.get("eff", 0) or 0)
    term = int(values.get("term", 0) or 0)
    bank_name = values.get("bank_name") or "Santander Consumer Bank"

    try:
        service_fee = Decimal(str(values.get("service_fee_eur")))
    except Exception:
        service_fee = Decimal("170.00")

    # 2. Вызываем новую математику
    monthly_payment_val, total_interest_val, schedule_list = calculate_amortization_schedule(amount, tan, term)
    total_debt_val = amount + total_interest_val

    # 3. Собираем контекст для шаблона (включая абсолютные пути к картинкам)
    context = {
        "client": client,
        "date_now": now_de_date(),
        "bank_name": bank_name,
        "company_legal": COMPANY["legal"],
        "company_addr": COMPANY["addr"],
        "company_reg": COMPANY["reg"],
        "company_contact": COMPANY["contact"],
        "company_email": COMPANY["email"],
        "company_web": COMPANY["web"],

        # Финансы
        "amount": fmt_eur_de_with_cents(amount),
        "tan": f"{tan:.2f}".replace(".", ","),
        "eff": f"{eff:.2f}".replace(".", ","),
        "term": term,
        "monthly_payment": fmt_eur_de_with_cents(monthly_payment_val),
        "service_fee": fmt_eur_de_with_cents(float(service_fee)),
        "total_interest": fmt_eur_de_with_cents(total_interest_val),
        "total_debt": fmt_eur_de_with_cents(total_debt_val),
        "schedule": schedule_list,

        # Пути к картинкам (WeasyPrint требует абсолютных путей)
        "logo_higobi": os.path.abspath(ASSETS["logo_higobi"]),
        "logo_partner1": os.path.abspath(ASSETS["logo_partner1"]),
        "logo_partner2": os.path.abspath(ASSETS["logo_partner2"]),
        "logo_santa": os.path.abspath(ASSETS["logo_santa"]),
        "sign_bank": os.path.abspath(ASSETS["sign_bank"]),
        "sign_c2g": os.path.abspath(ASSETS["sign_c2g"]),
    }

    # 4. Рендерим HTML
    # Убедитесь, что contract_template.html лежит в корневой папке бота
    env = Environment(loader=FileSystemLoader(str(BASE_DIR)))
    template = env.get_template('contract_template.html')
    rendered_html = template.render(context)

    # 5. Генерируем PDF и возвращаем байты
    # base_url нужен для корректного поиска локальных файлов (картинок)
    pdf_bytes = HTML(string=rendered_html, base_url=str(BASE_DIR)).write_pdf()

    return pdf_bytes

# ---------- SEPA PDF ----------
class Typesetter:
    def __init__(self, canv, left=18*mm, top=None, line_h=14.2):
        self.c = canv
        self.left = left
        self.x = left
        self.y = top if top is not None else A4[1] - 18*mm
        self.line_h = line_h
        self.font_r = F_MONO
        self.font_b = F_MONO_B
        self.size = 11
    def _w(self, s, bold=False, size=None):
        size = size or self.size
        return pdfmetrics.stringWidth(s, self.font_b if bold else self.font_r, size)
    def nl(self, n=1):
        self.x = self.left; self.y -= self.line_h * n
    def seg(self, t, bold=False, size=None):
        size = size or self.size
        self.c.setFont(self.font_b if bold else self.font_r, size)
        self.c.drawString(self.x, self.y, t)
        self.x += self._w(t, bold, size)
    def line(self, t="", bold=False, size=None):
        self.seg(t, bold, size); self.nl()
    def para(self, text, bold=False, size=None, indent=0, max_w=None):
        size = size or self.size
        max_w = max_w or (A4[0] - self.left*2)
        words = text.split()
        line = ""; first = True
        while words:
            w = words[0]; trial = (line + " " + w).strip()
            if self._w(trial, bold, size) <= max_w - (indent if first else 0):
                line = trial; words.pop(0)
            else:
                self.c.setFont(self.font_b if bold else self.font_r, size)
                x0 = self.left + (indent if first else 0)
                self.c.drawString(x0, self.y, line)
                self.y -= self.line_h; first = False; line = ""
        if line:
            self.c.setFont(self.font_b if bold else self.font_r, size)
            x0 = self.left + (indent if first else 0)
            self.c.drawString(x0, self.y, line)
            self.y -= self.line_h
    def kv(self, label, value, size=None, max_w=None):
        size = size or self.size
        max_w = max_w or (A4[0] - self.left*2)
        label_txt = f"{label}: "; lw = self._w(label_txt, True, size)
        self.c.setFont(self.font_b, size); self.c.drawString(self.left, self.y, label_txt)
        rem_w = max_w - lw; old_left = self.left; self.left += lw
        self.para(value, bold=False, size=size, indent=0, max_w=rem_w)
        self.left = old_left

def sepa_build_pdf(values: dict) -> bytes:
    name = (values.get("name","") or "").strip() or "______________________________"
    addr = (values.get("addr","") or "").strip() or "_______________________________________________________"
    capcity = (values.get("capcity","") or "").strip() or "__________________________________________"
    country = (values.get("country","") or "").strip() or "____________________"
    idnum = (values.get("idnum","") or "").strip() or "________________"
    iban = ((values.get("iban","") or "").replace(" ", "")) or "__________________________________"
    bic  = (values.get("bic","") or "").strip() or "___________"

    date_de = now_de_date()
    umr = f"GAFNER-{datetime.now().year}-2690497"

    bank_name = values.get("bank_name") or "Santander Consumer Bank"
    bank_addr = values.get("bank_addr") or ""

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    ts = Typesetter(c, left=18*mm, top=A4[1]-22*mm, line_h=14.2)
    ts.size = 11

    ts.line("SEPA-Lastschriftmandat (SDD)", bold=True)
    ts.seg("Schema: ", True); ts.seg("Y CORE   X B2B   ")
    ts.seg("Zahlungsart: ", True); ts.line("Y Wiederkehrend   X Einmalig")

    ts.kv("Gläubiger-Identifikationsnummer (CI)", SEPA["ci"])
    ts.kv("Mandatsreferenz (UMR)", umr)
    ts.nl()

    ts.line("Zahlerdaten (Kontoinhaber)", bold=True)
    ts.kv("Name/Firma", name)
    ts.kv("Anschrift", addr)
    ts.kv("PLZ / Ort / Bundesland", capcity)
    ts.kv("Land", country + "    Ausweis-/Steuer-Nr.: " + idnum)
    ts.kv("IBAN (ohne Leerzeichen)", iban)
    ts.kv("BIC", bic)
    ts.nl()

    ts.line("Ermächtigung", bold=True)
    ts.para(
        "Mit meiner Unterschrift ermächtige ich (A) "
        f"{bank_name}, an meine Bank Lastschriftaufträge zu senden und (B) "
        "meine Bank, mein Konto gemäß den Anweisungen des Kreditgebers zu belasten.",
    )
    ts.para(
        "Für das Schema CORE habe ich das Recht, bei meiner Bank die Erstattung "
        "innerhalb von 8 Wochen ab Belastungsdatum zu verlangen.",
    )
    ts.kv("Pre-Notification", f"{SEPA['prenotice_days']} Tage vor Fälligkeit")
    ts.kv("Datum", date_de)
    ts.para("Unterschrift des Zahlers: nicht erforderlich; Dokumente werden durch den Intermediär vorbereitet.")
    ts.nl()

    ts.line("Daten des Gläubigers", bold=True)
    ts.kv("Bezeichnung", bank_name)
    ts.kv("Adresse", bank_addr)
    ts.kv("SEPA CI", SEPA["ci"])
    ts.nl()

    ts.line("Beauftragter für die Sammlung des Mandats (Intermediär)", bold=True)
    ts.kv("Name", COMPANY["legal"])
    ts.kv("Adresse", COMPANY["addr"])
    ts.kv("Kontakt", f"{COMPANY['contact']} | E-Mail: {COMPANY['email']} | Web: {COMPANY['web']}")
    ts.nl()

    ts.line("Optionale Klauseln", bold=True)
    ts.para("[Y] Ich erlaube die elektronische Aufbewahrung dieses Mandats.")
    ts.para("[Y] Bei Änderung der IBAN oder Daten verpflichte ich mich, dies schriftlich mitzuteilen.")
    ts.para("[Y] Widerruf: Das Mandat kann durch Mitteilung an den Kreditgeber und meine Bank widerrufen werden; "
            "der Widerruf hat Wirkung auf zukünftige Abbuchungen.")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()

# ---------- AML LETTER ----------
def aml_build_pdf(values: dict) -> bytes:
    name = (values.get("aml_name","") or "").strip() or "[_____________________________]"
    idn  = (values.get("aml_id","") or "").strip() or "[________________]"
    iban = ((values.get("aml_iban","") or "").replace(" ","")) or "[_____________________________]"
    date_de = now_de_date()

    VORGANG_NR = "2690497"
    PAY_DEADLINE   = 7
    PAY_AMOUNT     = Decimal("285.00")

    bank_name = values.get("bank_name") or "Santander Consumer Bank"
    bank_addr = values.get("bank_addr") or ""
    BANK_DEPT  = "Abteilung Sicherheit & Antibetrug"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm,
        topMargin=14*mm, bottomMargin=14*mm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H",      fontName=F_MONO_B, fontSize=13.4, leading=15.2, spaceAfter=4))
    styles.add(ParagraphStyle(name="Hsub",   fontName=F_MONO,   fontSize=10.2, leading=12.0, textColor=colors.HexColor("#334")))
    styles.add(ParagraphStyle(name="H2",     fontName=F_MONO_B, fontSize=12.2, leading=14.0, spaceBefore=5, spaceAfter=3))
    styles.add(ParagraphStyle(name="Mono",   fontName=F_MONO,   fontSize=10.6, leading=12.6))
    styles.add(ParagraphStyle(name="MonoSm", fontName=F_MONO,   fontSize=10.0, leading=11.8))
    styles.add(ParagraphStyle(name="Key",    fontName=F_MONO_B, fontSize=10.6, leading=12.6))
    styles.add(ParagraphStyle(name="Box",    fontName=F_MONO,   fontSize=10.2, leading=12.0))

    page1 = []
    logo = img_box(ASSETS["logo_partner1"], 26*mm)
    if logo:
        logo.hAlign = "CENTER"
        page1 += [logo, Spacer(1, 6)]

    page1.append(Paragraph(f"{bank_name} – Zahlungsaufforderung", styles["H"]))
    page1.append(Paragraph(BANK_DEPT, styles["Hsub"]))
    page1.append(Paragraph(f"Vorgang-Nr.: {VORGANG_NR}", styles["MonoSm"]))
    page1.append(Paragraph(f"Datum: {date_de}", styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    warn_icon_l = exclam_flowable(10 * mm)
    warn_icon_r = exclam_flowable(10 * mm)
    preamble_text = (
        "Nach einer erneuten internen Prüfung (deren Verfahren und Methodik nicht offengelegt werden) "
        "wurde Ihr Profil vom Kreditgeber einer erhöhten Wahrscheinlichkeit von Zahlungsverzug bzw. "
        "-ausfall zugeordnet. Zur Risikosteuerung und zur Fortführung des Auszahlungsprozesses ist eine "
        f"<b>Garantiezahlung/Versicherungsprämie in Höhe von {fmt_eur(PAY_AMOUNT)}</b> erforderlich, zahlbar "
        f"<b>innerhalb von {PAY_DEADLINE} Werktagen</b>."
    )
    pre_tbl = Table(
        [[warn_icon_l or "", Paragraph(preamble_text, styles["MonoSm"]), warn_icon_r or ""]],
        colWidths=[12*mm, doc.width - 24*mm, 12*mm]
    )
    pre_tbl.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("ALIGN",(0,0),(0,0),"CENTER"),
        ("ALIGN",(2,0),(2,0),"CENTER"),
        ("BOX",(0,0),(-1,-1),0.8,colors.HexColor("#E0A800")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#FFF7E6")),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),6),  ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    page1 += [pre_tbl, Spacer(1, 6)]

    page1.append(Paragraph(f"<b>Adressat (Intermediär):</b> {COMPANY['legal']}", styles["Mono"]))
    page1.append(Paragraph(COMPANY["addr"], styles["MonoSm"]))
    page1.append(Paragraph(f"Kontakt: {COMPANY['contact']} | E-Mail: {COMPANY['email']} | Web: {COMPANY['web']}",
                           styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    page1.append(Paragraph(
        "Im Anschluss an eine ergänzende interne Prüfung zum oben genannten Vorgang teilen wir Folgendes mit.",
        styles["Mono"]
    ))
    page1.append(Spacer(1, 5))

    page1.append(Paragraph("Daten des Antragstellers (zur Identifizierung)", styles["H2"]))
    for line in [
        f"• <b>Name und Nachname:</b> {name}",
        f"• <b>ID/Steuer-Nr. (falls vorhanden):</b> {idn}",
        f"• <b>IBAN des Kunden:</b> {iban}",
    ]:
        page1.append(Paragraph(line, styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    page1.append(Paragraph("1) Zahlung angefordert", styles["H2"]))
    for b in [
        "• <b>Typologie:</b> Garantiezahlung / Versicherungsprämie",
        f"• <b>Betrag:</b> {fmt_eur(PAY_AMOUNT)}",
        f"• <b>Frist der Ausführung:</b> innerhalb von {PAY_DEADLINE} Werktagen ab Erhalt dieses Schreibens",
        "• <b>Ausführungsweise:</b> Zahlungskoordinaten werden dem Kunden unmittelbar vom zuständigen "
        "Manager der GafnerImmo Credit UG mitgeteilt (keine Zahlungen an Dritte).",
        "• <b>Zahlungspflichtiger:</b> der Antragsteller (Кunde)",
    ]:
        page1.append(Paragraph(b, styles["MonoSm"]))
    page1.append(Spacer(1, 5))

    page1.append(Paragraph("2) Natur der Anforderung", styles["H2"]))
    page1.append(Paragraph(
        "Diese Anforderung ist verpflichtend, vorgelagert und nicht verhandelbar. "
        "Die betreffende Zahlung ist eine notwendige Voraussetzung für die Fortführung des Auszahlungsprozesses.",
        styles["MonoSm"]
    ))
    page1.append(Spacer(1, 5))

    page1.append(Paragraph("3) Pflichten des Intermediärs", styles["H2"]))
    for b in [
        "• Den Antragsteller über dieses Schreiben informieren und Rückmeldung einholen.",
        "• Zahlungskoordinaten bereitstellen und die Vereinnahmung/Weiterleitung gemäß Bankanweisungen vornehmen.",
        "• Zahlungsnachweis (Auftrags-/Quittungskopie) an die Bank übermitteln und mit Kundendaten "
        "(Name und Nachname ↔ IBAN) abgleichen.",
        "• Kommunikation mit der Bank im Namen und für Rechnung des Kunden führen.",
    ]:
        page1.append(Paragraph(b, styles["MonoSm"]))
    page1.append(Spacer(1, 6))

    page2 = []
    page2.append(Paragraph("4) Folgen bei Nichtzahlung", styles["H2"]))
    page2.append(Paragraph(
        "Bei ausbleibender Zahlung innerhalb der genannten Frist lehnt die Bank die Auszahlung einseitig ab "
        "und schließt den Vorgang, mit Widerruf etwaiger Vorbewertungen/Vorbestätigungen und Aufhebung der "
        "zugehörigen wirtschaftlichen Bedingungen.",
        styles["MonoSm"]
    ))
    page2.append(Spacer(1, 6))

    info = ("Zahlungskoordinaten werden dem Kunden direkt vom zuständigen Manager der "
            "GafnerImmo Credit UG bereitgestellt. Bitte leisten Sie keine Zahlungen an Dritte "
            "oder abweichende Konten.")
    info_box = Table([[Paragraph(info, styles["Box"])]], colWidths=[doc.width])
    info_box.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.8,colors.HexColor("#96A6C8")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EEF3FF")),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    page2.append(info_box)
    page2.append(Spacer(1, 8))

    page2.append(Paragraph(bank_name, styles["Key"]))
    page2.append(Paragraph(BANK_DEPT, styles["MonoSm"]))
    page2.append(Paragraph(f"Adresse: {bank_addr}", styles["MonoSm"]))

    story = []
    story.extend(page1)
    story.append(PageBreak())
    story.extend(page2)

    doc.build(story, onFirstPage=draw_border_and_pagenum, onLaterPages=draw_border_and_pagenum)
    buf.seek(0)
    return buf.read()

# ---------- НОТАРИАЛЬНЫЙ PDF (оверлей) ----------
def notary_replace_amount_pdf_purepy(base_pdf_path: str, new_amount_float: float) -> bytes:
    import io, os, re
    from statistics import median
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer, LTTextLine, LTChar
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.colors import white, black
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from pypdf import PdfReader, PdfWriter

    FONT_CANDIDATES = {
        "TimesNewRomanPS": {
            "regular": "fonts/TimesNewRomanPSMT.ttf",
            "bold": "fonts/TimesNewRomanPS-BoldMT.ttf",
            "italic": "fonts/TimesNewRomanPS-ItalicMT.ttf",
            "bolditalic": "fonts/TimesNewRomanPS-BoldItalicMT.ttf",
        },
        "NimbusRomNo9L": {
            "regular": "fonts/NimbusRomNo9L-Regu.ttf",
            "bold": "fonts/NimbusRomNo9L-Medi.ttf",
            "italic": "fonts/NimbusRomNo9L-RegIta.ttf",
            "bolditalic": "fonts/NimbusRomNo9L-MedIta.ttf",
        },
        "DejaVuSerif": {
            "regular": "fonts/DejaVuSerif.ttf",
            "bold": "fonts/DejaVuSerif-Bold.ttf",
            "italic": "fonts/DejaVuSerif-Italic.ttf",
            "bolditalic": "fonts/DejaVuSerif-BoldItalic.ttf",
        },
    }

    _registered = {}

    def _strip_subset(fn: str) -> str:
        return re.sub(r"^[A-Z]{6}\+", "", fn or "")

    def _family_and_style(fontname: str):
        base = _strip_subset(fontname)
        low = base.lower()
        bold = ("bold" in low) or ("medi" in low) or ("demi" in low)
        italic = ("italic" in low) or ("oblique" in low) or ("ita" in low)
        style = "bolditalic" if (bold and italic) else ("bold" if bold else ("italic" if italic else "regular"))
        if "timesnewroman" in low: fam = "TimesNewRomanPS"
        elif "nimbusrom" in low:   fam = "NimbusRomNo9L"
        elif "dejavuserif" in low: fam = "DejaVuSerif"
        elif "times" in low:       fam = "NimbusRomNo9L"
        else:                       fam = "NimbusRomNo9L"
        return fam, style

    def _ensure_font(family: str, style: str) -> str:
        key = f"{family}-{style}"
        if key in _registered:
            return _registered[key]
        path = FONT_CANDIDATES.get(family, {}).get(style)
        if path and os.path.exists(path):
            rl_name = f"{family}_{style}"
            try:
                pdfmetrics.registerFont(TTFont(rl_name, path))
                _registered[key] = rl_name
                return rl_name
            except Exception:
                pass
        fb = "Times-Roman" if style in ("regular", "italic") else "Times-Bold"
        _registered[key] = fb
        return fb

    def _format_like(src: str, value: float) -> str:
        s = src.strip()
        eur_left = s.startswith("€")
        has_cents = ("," in s)
        if "\u00A0" in s or " " in s: sep = " "
        elif "." in s:               sep = "."
        else:                        sep = ""
        n = abs(value)
        i = f"{int(n):,}".replace(",", ".")
        if sep == " ": i = i.replace(".", " ")
        if sep == "":  i = i.replace(".", "")
        if has_cents:
            frac = f"{n:.2f}".split(".")[1]
            num = f"{i},{frac}"
        else:
            num = i
        return f"€ {num}" if eur_left else f"{num} €"

    date_pat = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
    current_date = now_de_date()

    money_pats = [
        re.compile(r"5[.\s\u00A0]?000(?:,00)?\s?€"),
        re.compile(r"€\s?5[.\s\u00A0]?000(?:,00)?"),
    ]

    matches_by_page = {}

    for pageno, layout in enumerate(extract_pages(base_pdf_path)):
        page_hits = []
        for box in layout:
            if not isinstance(box, LTTextContainer):
                continue
            for line in box:
                if not isinstance(line, LTTextLine):
                    continue
                chars = [ch for ch in line if isinstance(ch, LTChar)]
                if not chars:
                    continue
                txt = "".join(c.get_text() for c in chars)

                for pat in money_pats:
                    for m in pat.finditer(txt):
                        a, b = m.span()
                        seg = chars[a:b]
                        if not seg: continue
                        x0 = min(c.x0 for c in seg); x1 = max(c.x1 for c in seg)
                        y0 = min(c.y0 for c in seg); y1 = max(c.y1 for c in seg)
                        sizes = [c.size for c in seg]; base_size = float(median(sizes))
                        fontname = seg[0].fontname
                        fam, style = _family_and_style(fontname)
                        k = float(os.getenv("NOTARY_OVERLAY_PCT", "0.265"))
                        page_hits.append({
                            "kind": "amount",
                            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                            "size": base_size, "family": fam, "style": style,
                            "src": m.group(0), "k": k
                        })

                for m in date_pat.finditer(txt):
                    a, b = m.span()
                    seg = chars[a:b]
                    if not seg: continue
                    x0 = min(c.x0 for c in seg); x1 = max(c.x1 for c in seg)
                    y0 = min(c.y0 for c in seg); y1 = max(c.y1 for c in seg)
                    sizes = [c.size for c in seg]; base_size = float(median(sizes))
                    fontname = seg[0].fontname
                    fam, style = _family_and_style(fontname)
                    k = float(os.getenv("NOTARY_OVERLAY_PCT", "0.265"))
                    page_hits.append({
                        "kind": "date",
                        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                        "size": base_size, "family": fam, "style": style,
                        "src": m.group(0), "k": k
                    })
        if page_hits:
            matches_by_page[pageno] = page_hits

    reader = PdfReader(base_pdf_path)
    overlay = io.BytesIO()
    canv = None

    for i, page in enumerate(reader.pages):
        w = float(page.mediabox.width); h = float(page.mediabox.height)
        if i == 0:
            canv = rl_canvas.Canvas(overlay, pagesize=(w, h))

        for hit in matches_by_page.get(i, []):
            x0, y0, x1, y1 = hit["x0"], hit["y0"], hit["x1"], hit["y1"]
            size = hit["size"]
            rl_font = _ensure_font(hit["family"], hit["style"])
            new_text = _format_like(hit["src"], new_amount_float) if hit["kind"] == "amount" else current_date

            pad = max(1.2, 0.18 * size)
            rect_w_min = (x1 - x0) + 2 * pad
            rect_h = (y1 - y0) + 2 * pad
            canv.setFillColor(white); canv.setStrokeColor(white)
            canv.rect(x0 - pad, y0 - pad, rect_w_min, rect_h, fill=1, stroke=0)

            canv.setFillColor(black); canv.setStrokeColor(black)
            try:
                text_w = pdfmetrics.stringWidth(new_text, rl_font, size)
            except Exception:
                rl_font = "Times-Roman"
                text_w = pdfmetrics.stringWidth(new_text, rl_font, size)

            target_w = (x1 - x0)
            charspace = 0.0
            if len(new_text) > 1:
                charspace = (target_w - text_w) / (len(new_text) - 1)
                charspace = max(min(charspace, 1.2), -0.6)

            base_y = y0 + (y1 - y0) * hit["k"]
            textobj = canv.beginText()
            textobj.setTextOrigin(x0, base_y)
            textobj.setFont(rl_font, size)
            try:
                textobj.setCharSpace(charspace)
            except Exception:
                pass
            textobj.textOut(new_text)
            canv.drawText(textobj)
        canv.showPage()

    canv.save()
    overlay.seek(0)

    over_reader = PdfReader(overlay)
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i < len(over_reader.pages):
            page.merge_page(over_reader.pages[i])
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out); out.seek(0)
    return out.read()

# ---------- НОВЫЙ ДОКУМЕНТ: Письмо-подтверждение (с печатью и подписью) ----------
def bank_confirmation_build_pdf(values: dict) -> bytes:
    """
    Письмо от Santander → HIGOBI с подтверждением одобрения.
    Использует логотип assets/santa.png (ASSETS['logo_santa']).
    НИЖНЯЯ ПРАВАЯ ОБЛАСТЬ: печать (santastamp.png) + подпись (kirk.png), подпись поверх печати.
    """
    client = (values.get("client","") or "").strip() or "PLACEHOLDER"
    amount = float(values.get("amount", 0) or 0)
    tan    = float(values.get("tan", 0) or 0)
    term   = int(values.get("term", 0) or 0)

    bank_name = values.get("bank_name") or "Santander Consumer Bank AG"
    bank_addr = values.get("bank_addr") or ""
    dept = "Bereich Konsumentenkredite"

    service_fee = values.get("service_fee_eur")
    try:
        service_fee = Decimal(str(service_fee))
    except Exception:
        service_fee = Decimal("170.00")

    fee_line_words = ""
    if service_fee.quantize(Decimal("0.01")) == Decimal("170.00"):
        fee_line_words = " (in Worten: einhundertsiebzig Euro)"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=17*mm, rightMargin=17*mm,
        topMargin=15*mm,  bottomMargin=14*mm
    )

    st = getSampleStyleSheet()
    st.add(ParagraphStyle(name="H",      fontName=F_MONO_B, fontSize=13.4, leading=15.2, spaceAfter=4))
    st.add(ParagraphStyle(name="Mono",   fontName=F_MONO,   fontSize=10.6, leading=12.6))
    st.add(ParagraphStyle(name="MonoSm", fontName=F_MONO,   fontSize=10.0, leading=11.6))
    st.add(ParagraphStyle(name="Key",    fontName=F_MONO_B, fontSize=10.6, leading=12.6))
    st.add(ParagraphStyle(name="Subtle", fontName=F_MONO,   fontSize=9.6,  leading=11.0, textColor=colors.HexColor("#333")))
    st.add(ParagraphStyle(name="H2",     fontName=F_MONO_B, fontSize=12.0, leading=14.0, spaceBefore=6, spaceAfter=4))

    story = []

    # Логотип (santa.png)
    logo = img_box(ASSETS["logo_santa"], 24*mm)
    if logo:
        logo.hAlign = "CENTER"
        story += [logo, Spacer(1, 6)]

    # Шапка Von/An
    head_tbl = Table([
        [Paragraph("<b>Von:</b>", st["Key"]), Paragraph(f"{bank_name}<br/>{dept}", st["Mono"])],
        [Paragraph("<b>An:</b>",  st["Key"]), Paragraph(f"{COMPANY['legal']}<br/>Kooperationspartner / Finanzvermittler", st["Mono"])],
    ], colWidths=[22*mm, doc.width-22*mm])
    head_tbl.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story += [head_tbl, Spacer(1, 4)]

    story.append(Paragraph(f"<b>Betreff:</b> Bestätigung der Kreditgenehmigung für den Kunden <b>{client}</b>", st["Mono"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Sehr geehrte Damen und Herren,", st["Mono"]))
    story.append(Spacer(1, 2))
    story.append(Paragraph(
        f"hiermit bestätigen wir, dass der im Namen von <b>{client}</b> eingereichte Finanzierungsantrag "
        "von unserem Haus <b>positiv geprüft und genehmigt</b> wurde.",
        st["Mono"]
    ))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "Die Prüfung des Dossiers wurde gemäß den geltenden Rechtsnormen der Bundesrepublik Deutschland und "
        "der Europäischen Union durchgeführt, insbesondere: <b>§§ 491 ff.</b> sowie <b>§ 505a BGB</b> "
        "(Kreditwürdigkeitsprüfung), dem <b>Kreditwesengesetz (KWG)</b>, der <b>Verordnung (EU) Nr. 575/2013 (CRR)</b>, "
        "dem <b>Geldwäschegesetz (GwG)</b> sowie den Anforderungen der <b>DSGVO/BDSG</b> und den einschlägigen <b>MaRisk</b>.",
        st["MonoSm"]
    ))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Konditionen der genehmigten Finanzierung:</b>", st["H2"]))
    cond = [
        f"• <b>Kreditbetrag:</b> {fmt_eur_de_with_cents(amount)}",
        f"• <b>Zinssatz (jährlich, Sollzins):</b> {tan:.2f} %",
        f"• <b>Laufzeit:</b> {term} Monate",
        "• <b>Auszahlungsweg:</b> Banküberweisung",
        "• <b>Voraussichtliche Gutschrift der Mittel:</b> innerhalb von bis zu 60 Minuten nach Vertragsunterzeichnung und Aktivierung des Dossiers",
    ]
    for c in cond:
        story.append(Paragraph(c, st["MonoSm"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Nächster Schritt (Aktivierung und Abschluss):</b>", st["H2"]))
    story.append(Paragraph(
        "Gemäß dem festgelegten Kooperationsverfahren zwischen der Santander Consumer Bank AG und der "
        "GafnerImmo Credit UG ist für die finale Aktivierung und den Abschluss der Auszahlung die Zahlung "
        f"einer administrativen Service- und Vermittlungsgebühr in Höhe von {fmt_eur_de_with_cents(service_fee)}{fee_line_words} erforderlich.",
        st["MonoSm"]
    ))
    story.append(Spacer(1, 3))
    story.append(Paragraph("<b>Diese Gebühr deckt insbesondere ab:</b>", st["Mono"]))
    for line in [
        "• Prüfung und Validierung der Kundendokumente;",
        "• Erstellung и rechtliche Finalisierung des personalisierten Kreditvertrags;",
        "• administrative Abstimmung zwischen Bank und Vermittler;",
        "• sichere Identifizierung des Kunden und Prüfungen gegen Sanktionslisten",
    ]:
        story.append(Paragraph(line, st["MonoSm"]))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "Die Zahlung ist unmittelbar auf die vom Manager der GafnerImmo Credit UG als autorisiertem "
        "Kooperationspartner bereitgestellten Bankverbindungsdaten zu leisten.",
        st["MonoSm"]
    ))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "Wir bitten, den Kunden über das positive Ergebnis und die Notwendigkeit der Zahlung der genannten "
        "Gebühr für eine zügige Aktivierung zu informieren.",
        st["MonoSm"]
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Mit freundlichen Grüßen", st["Mono"]))
    story.append(Paragraph("Santander Consumer Bank AG", st["Key"]))
    story.append(Paragraph(dept, st["Subtle"]))

    # --- Абсолютная отрисовка печати и подписи на странице (поверх контента) ---
    def _on_page(canv, _doc):
        # рамка + номер
        draw_border_and_pagenum(canv, _doc)
        try:
            page_w, page_h = _doc.pagesize
            # размеры и позиция нижнего правого блока
            stamp_w = 78 * mm   # ширина печати
            stamp_h = 56 * mm   # высота печати
            right_margin = _doc.rightMargin
            # Позиционируем внутри внутренней рамки, над номером страницы
            x_stamp = page_w - right_margin - stamp_w
            y_stamp = 22 * mm   # ~ как на скриншоте, над номером страницы

            # печать
            canv.drawImage(
                ASSETS["stamp_santa"], x_stamp, y_stamp,
                width=stamp_w, height=stamp_h,
                preserveAspectRatio=True, mask="auto"
            )

            # подпись — поверх печати, чуть смещена вниз
            sign_w = 50 * mm
            sign_h = 22 * mm
            x_sign = x_stamp + (stamp_w - sign_w) / 2
            y_sign = y_stamp + (stamp_h - sign_h) / 2 - 3 * mm
            canv.drawImage(
                ASSETS["sign_kirk"], x_sign, y_sign,
                width=sign_w, height=sign_h,
                preserveAspectRatio=True, mask="auto"
            )
        except Exception as e:
            log.warning("Stamp/Signature overlay failed: %s", e)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    buf.seek(0)
    return buf.read()

# ---------- CARD DOC ----------
def card_build_pdf(values: dict) -> bytes:
    name = (values.get("card_name","") or "").strip() or "______________________________"
    addr = (values.get("card_addr","") or "").strip() or "_______________________________________________________"

    case_num = "2690497"
    umr = f"GAFNER-{datetime.now().year}-2690497"

    date_de = now_de_date()
    bank_name = values.get("bank_name") or "Santander Consumer Bank"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16*mm, rightMargin=16*mm,
        topMargin=14*mm, bottomMargin=14*mm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1",    fontName=F_MONO_B, fontSize=14.2, leading=16.0, spaceAfter=6, alignment=1))
    styles.add(ParagraphStyle(name="H2",    fontName=F_MONO_B, fontSize=12.2, leading=14.0, spaceBefore=6, spaceAfter=4))
    styles.add(ParagraphStyle(name="Mono",  fontName=F_MONO,   fontSize=10.6, leading=12.6))
    styles.add(ParagraphStyle(name="MonoS", fontName=F_MONO,   fontSize=10.0, leading=11.8))
    styles.add(ParagraphStyle(name="Badge", fontName=F_MONO_B, fontSize=10.2, leading=12.0, textColor=colors.HexColor("#0B5D1E"), alignment=1))

    story = []
    logo = img_box(ASSETS["logo_partner1"], 26*mm)
    if logo:
        logo.hAlign = "CENTER"
        story += [logo, Spacer(1, 4)]

    story.append(Paragraph(f"{bank_name} – Auszahlung per Karte", styles["H1"]))
    meta = Table([
        [Paragraph(f"Datum: {date_de}", styles["MonoS"]), Paragraph(f"Vorgang-Nr.: {case_num}", styles["MonoS"])],
    ], colWidths=[doc.width/2.0, doc.width/2.0])
    meta.setStyle(TableStyle([
        ("ALIGN",(0,0),(0,0),"LEFT"), ("ALIGN",(1,0),(1,0),"RIGHT"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story += [meta]

    badge = Table([[Paragraph("BESTÄTIGT – Operatives Dokument", styles["Badge"])]], colWidths=[doc.width])
    badge.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.9,colors.HexColor("#B9E8C8")),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EFFEFA")),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story += [badge, Spacer(1, 6)]

    intro = (
        "Um die Verfügbarkeit der Mittel noch heute zu gewährleisten und aufgrund nicht erfolgreicher "
        "automatischer Überweisungsversuche wird die Bank – ausnahmsweise – eine "
        "<b>personalisierte Kreditkarte</b> ausstellen, mit Zustellung <b>bis 24:00</b> an die im SDD-Mandat "
        "angegebene Adresse."
    )
    story.append(Paragraph(intro, styles["Mono"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Identifikationsdaten (auszufüllen)", styles["H2"]))
    story.append(Paragraph(f"• <b>Name des Kunden:</b> {name}", styles["MonoS"]))
    story.append(Paragraph(f"• <b>Lieferadresse (aus SDD):</b> {addr}", styles["MonoS"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Was ist jetzt zu tun", styles["H2"]))
    for line in [
        "1) Anwesenheit an der Adresse bis 24:00; Ausweis bereithalten.",
        "2) Übergabe und Unterschrift bei Erhalt der Karte.",
        "3) Aktivierung mit OTP, das an die Kontakte des Kunden gesendet wird.",
        "4) Mittel vorab gutgeschrieben – unmittelbar nach Aktivierung verfügbar.",
        "5) Überweisung auf Kunden-IBAN per Banktransfer.",
    ]:
        story.append(Paragraph(line, styles["MonoS"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Betriebsbedingungen", styles["H2"]))
    cond = [
        "• <b>Kartenausgabegebühr:</b> 290 € (Produktion + Expresszustellung).",
        "• <b>Erste 5 ausgehende Verfügungen:</b> ohne Kommissionen; danach gemäß Standardtarif.",
        "• <b>Verrechnung der 290 €:</b> Betrag wird mit der ersten Rate verrechnet; "
        "falls die Rate < 290 € ist, wird der Rest mit den folgenden Raten bis zur vollständigen "
        "Verrechnung ausgeglichen (Anpassung erscheint im Tilgungsplan, ohne Erhöhung der Gesamtkosten des Kredits).",
        "• <b>Finanzfluss und Koordinaten:</b> werden von <b>GafnerImmo Credit UG</b> verwaltet; "
        "Zahlungskoordinaten (falls erforderlich) werden ausschließlich von GafnerImmo Credit UG bereitgestellt.",
    ]
    for p in cond:
        story.append(Paragraph(p, styles["MonoS"]))
    story.append(Spacer(1, 6))

    tech = Table([
        [Paragraph(f"Praktik: {case_num}", styles["MonoS"]), Paragraph(f"UMR: {umr}", styles["MonoS"])],
        [Paragraph(f"Adresse (SDD): {addr}", styles["MonoS"]), Paragraph("", styles["MonoS"])],
    ], colWidths=[doc.width*0.62, doc.width*0.38])
    tech.setStyle(TableStyle([
        ("GRID",(0,0),(-1,-1),0.3,colors.lightgrey),
        ("BACKGROUND",(0,0),(-1,-1),colors.whitesmoke),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),5), ("RIGHTPADDING",(0,0),(-1,-1),5),
        ("TOPPADDING",(0,0),(-1,-1),2),  ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ]))
    story += [tech, Spacer(1, 6)]

    story.append(Paragraph("Unterschriften", styles["H2"]))
    sig_head_l = Paragraph("Unterschrift Kunde", styles["MonoS"])
    sig_head_c = Paragraph("Unterschrift Vertreter<br/>Bank", styles["MonoS"])
    sig_head_r = Paragraph("Unterschrift Vertreter<br/>GafnerImmo Credit UG", styles["MonoS"])
    sig_bank = img_box(ASSETS["sign_bank"], 22*mm)
    sig_c2g  = img_box(ASSETS["sign_c2g"],  22*mm)
    SIG_H = 24*mm
    sig_tbl = Table(
        [
            [sig_head_l, sig_head_c, sig_head_r],
            ["", sig_bank or Spacer(1, SIG_H), sig_c2g or Spacer(1, SIG_H)],
            ["", "", ""],
        ],
        colWidths=[doc.width/3.0, doc.width/3.0, doc.width/3.0],
        rowHeights=[9*mm, SIG_H, 6*mm],
        hAlign="CENTER",
    )
    sig_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,0),"CENTER"),
        ("VALIGN",(0,1),(-1,1),"BOTTOM"),
        ("BOTTOMPADDING",(0,1),(-1,1),-6),
        ("LINEBELOW",(0,2),(0,2),1.0,colors.black),
        ("LINEBELOW",(1,2),(1,2),1.0,colors.black),
        ("LINEBELOW",(2,2),(2,2),1.0,colors.black),
    ]))
    story.append(sig_tbl)

    def _on_page(canv, _doc):
        draw_border_and_pagenum(canv, _doc)
        try:
            page_w, page_h = _doc.pagesize
            stamp_w = 78 * mm
            stamp_h = 56 * mm
            x_stamp = page_w - _doc.rightMargin - stamp_w
            y_stamp = 22 * mm

            canv.drawImage(
                ASSETS["stamp_santa"], x_stamp, y_stamp,
                width=stamp_w, height=stamp_h,
                preserveAspectRatio=True, mask="auto"
            )

            sign_w = 50 * mm
            sign_h = 22 * mm
            x_sign = x_stamp + (stamp_w - sign_w) / 2
            y_sign = y_stamp + (stamp_h - sign_h) / 2 - 3 * mm
            canv.drawImage(
                ASSETS["sign_kirk"], x_sign, y_sign,
                width=sign_w, height=sign_h,
                preserveAspectRatio=True, mask="auto"
            )
        except Exception as e:
            log.warning("Stamp/Signature overlay failed: %s", e)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    buf.seek(0)
    return buf.read()

# ---------- BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Выберите действие:", reply_markup=MAIN_KB)

def _ask_country_text():
    return "Под какую страну готовить документ? Ответьте: Германия или Австрия."

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == BTN_BOTH:
        context.user_data["flow"] = "both"
        await update.message.reply_text(_ask_country_text()); return ASK_COUNTRY
    if t == BTN_AML:
        context.user_data["flow"] = "aml"
        await update.message.reply_text(_ask_country_text()); return ASK_COUNTRY
    if t == BTN_CARD:
        context.user_data["flow"] = "card"
        await update.message.reply_text(_ask_country_text()); return ASK_COUNTRY
    if t == BTN_NOTARY:
        context.user_data["flow"] = "notary_pdf"
        await update.message.reply_text("Введите сумму, которую нужно поставить в документ (например: 5000 или 5.000,00):")
        return ASK_NOTARY_AMOUNT

    await update.message.reply_text("Нажмите одну из кнопок.", reply_markup=MAIN_KB)
    return ConversationHandler.END

def _parse_country(txt: str) -> str | None:
    s = (txt or "").strip().lower()
    if s in ("de", "германия", "germany", "deutschland"): return "DE"
    if s in ("at", "австрия", "austria", "österreich", "oesterreich"): return "AT"
    return None

async def ask_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cc = _parse_country(update.message.text)
    if not cc:
        await update.message.reply_text("Пожалуйста, укажите: Германия или Австрия."); return ASK_COUNTRY
    bp = get_bank_profile(cc)
    context.user_data["country"] = cc
    context.user_data["bank_name"] = bp["name"]
    context.user_data["bank_addr"] = bp["addr"]

    flow = context.user_data.get("flow")
    if flow in ("both",):
        await update.message.reply_text("Имя клиента (например: Mark Schneider)")
        return ASK_CLIENT
    if flow == "aml":
        await update.message.reply_text("АМЛ-комиссия: укажите ФИО (Name).")
        return AML_NAME
    if flow == "card":
        await update.message.reply_text("Выдача на карту: укажите ФИО клиента.")
        return CARD_NAME

    await update.message.reply_text("Неизвестный режим. Начните заново /start.")
    return ConversationHandler.END

# --- CONTRACT STEPS (используются и для BOTH)
async def ask_client(update, context):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Пожалуйста, укажите ФИО клиента."); return ASK_CLIENT
    context.user_data["client"] = name
    await update.message.reply_text("Сумма кредита (например: 12.000,00)")
    return ASK_AMOUNT

async def ask_amount(update, context):
    try:
        amount = parse_num(update.message.text)
        if amount <= 0: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректную сумму (например 12.000,00)"); return ASK_AMOUNT
    context.user_data["amount"] = amount
    await update.message.reply_text("Номинальная ставка Sollzins, % годовых (например 6,45)")
    return ASK_TAN

async def ask_tan(update, context):
    try:
        tan = parse_num(update.message.text)
        if tan < 0 or tan > 50: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректный Sollzins, например 6,45"); return ASK_TAN
    context.user_data["tan"] = tan
    await update.message.reply_text("Эффективная ставка Effektiver Jahreszins, % годовых (например 7,98)")
    return ASK_EFF

async def ask_eff(update, context):
    try:
        eff = parse_num(update.message.text)
        if eff < 0 or eff > 60: raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректный Effektiver Jahreszins, например 7,98"); return ASK_EFF
    context.user_data["eff"] = eff
    await update.message.reply_text("Срок (в месяцах, максимум 84)")
    return ASK_TERM

async def ask_term(update, context):
    try:
        term = int(parse_num(update.message.text))
        if term <= 0 or term > 84: raise ValueError
    except Exception:
        await update.message.reply_text("Введите срок от 1 до 84 месяцев"); return ASK_TERM
    context.user_data["term"] = term
    await update.message.reply_text("Какую сумму платежа выбираем? (например: 170, 170,00 или 1 250,50)")
    return ASK_FEE

async def ask_fee(update, context):
    try:
        fee = parse_money(update.message.text)
        if fee < 0 or fee > Decimal("1000000"):
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректную сумму, например: 170, 170,00 или 1 250,50")
        return ASK_FEE

    context.user_data["service_fee_eur"] = fee

    # Контракт
    pdf_bytes = build_contract_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename=f"Vorvertrag_{now_de_date().replace('.','')}.pdf"),
        caption="Готово. Контракт сформирован."
    )

    # Письмо-подтверждение (с печатью и подписью)
    pdf_bank = bank_confirmation_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bank), filename=f"Bestaetigung_Kreditgenehmigung_{now_de_date().replace('.','')}.pdf"),
        caption="Готово. Письмо-подтверждение банка сформировано."
    )

    # Переходим к SEPA (имя подставлено из контракта)
    if context.user_data.get("flow") == "both":
        context.user_data["name"] = context.user_data.get("client", "")
        await update.message.reply_text("Теперь данные для SEPA-мандата.\nУкажите адрес (улица/дом).")
        return SDD_ADDR

    return ConversationHandler.END

# --- SDD STEPS
async def sdd_name(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите ФИО/название."); return SDD_NAME
    context.user_data["name"] = v; await update.message.reply_text("Адрес (улица/дом)"); return SDD_ADDR

async def sdd_addr(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите адрес."); return SDD_ADDR
    context.user_data["addr"] = v; await update.message.reply_text("PLZ / Город / Земля (в одну строку)."); return SDD_CITY

async def sdd_city(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите PLZ / Город / Землю."); return SDD_CITY
    context.user_data["capcity"] = v; await update.message.reply_text("Страна."); return SDD_COUNTRY

async def sdd_country(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите страну."); return SDD_COUNTRY
    context.user_data["country"] = v; await update.message.reply_text("ID/Steuer-Nr. (если нет — «-»)"); return SDD_ID

async def sdd_id(update, context):
    v = (update.message.text or "").strip()
    context.user_data["idnum"] = "" if v == "-" else v
    await update.message.reply_text("IBAN (без пробелов)"); return SDD_IBAN

async def sdd_iban(update, context):
    iban = (update.message.text or "").replace(" ", "")
    if not iban: await update.message.reply_text("Введите IBAN (без пробелов)."); return SDD_IBAN
    context.user_data["iban"] = iban; await update.message.reply_text("BIC (если нет — «-»)"); return SDD_BIC

async def sdd_bic(update, context):
    bic = (update.message.text or "").strip()
    context.user_data["bic"] = "" if bic == "-" else bic
    pdf_bytes = sepa_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename=f"SEPA_Mandat_{now_de_date().replace('.','')}.pdf"),
        caption="Готово. SEPA-мандат сформирован."
    )
    return ConversationHandler.END

# --- AML FSM
async def aml_name(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите ФИО."); return AML_NAME
    context.user_data["aml_name"] = v; await update.message.reply_text("ID/Steuer-Nr. (если нет — «-»)"); return AML_ID

async def aml_id(update, context):
    v = (update.message.text or "").strip()
    context.user_data["aml_id"] = "" if v == "-" else v
    await update.message.reply_text("IBAN (без пробелов)"); return AML_IBAN

async def aml_iban(update, context):
    iban = (update.message.text or "").replace(" ", "")
    if not iban: await update.message.reply_text("Введите IBAN (без пробелов)."); return AML_IBAN
    context.user_data["aml_iban"] = iban
    pdf_bytes = aml_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="Sicherheitszahlung_Anforderung.pdf"),
        caption="Готово. Письмо (АМЛ/комплаенс) сформировано.",
    )
    return ConversationHandler.END

# --- CARD FSM
async def card_name(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите ФИО клиента."); return CARD_NAME
    context.user_data["card_name"] = v; await update.message.reply_text("Адрес доставки (из SDD): улица/дом, PLZ, город, земля."); return CARD_ADDR

async def card_addr(update, context):
    v = (update.message.text or "").strip()
    if not v: await update.message.reply_text("Укажите адрес доставки полностью."); return CARD_ADDR
    context.user_data["card_addr"] = v
    pdf_bytes = card_build_pdf(context.user_data)
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename="Auszahlung_per_Karte.pdf"),
        caption="Готово. Документ о выдаче на карту сформирован.",
    )
    return ConversationHandler.END

# --- NOTARY FSM
ASK_NOTARY_AMOUNT = 410
async def notary_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (update.message.text or "").strip()
    try:
        amt = float(parse_money(txt))
        if amt <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Введите корректную сумму (например: 5000 или 5.000,00).")
        return ASK_NOTARY_AMOUNT

    base_path = ASSETS.get("notary_pdf")
    if not base_path or not os.path.exists(base_path):
        await update.message.reply_text("Шаблон нотариального PDF не найден. Проверьте файл в /assets или /mnt/data.")
        return ConversationHandler.END

    try:
        pdf_bytes = notary_replace_amount_pdf_purepy(base_path, amt)
    except Exception as e:
        log.exception("NOTARY OVERLAY FAILED: %s", e)
        await update.message.reply_text("Ошибка при редактировании PDF. Проверьте шаблон/формат и попробуйте снова.")
        return ConversationHandler.END

    filename = f"Notarielle_Beglaubigung_edit_{now_de_date().replace('.','')}.pdf"
    await update.message.reply_document(
        document=InputFile(io.BytesIO(pdf_bytes), filename=filename),
        caption="Готово. Обновлённый документ."
    )
    return ConversationHandler.END

# ---------- BOOTSTRAP ----------
def main():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Env TELEGRAM_TOKEN is missing")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))

    conv_both = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_BOTH)), handle_menu)],
        states={
            ASK_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_country)],
            ASK_CLIENT:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_client)],
            ASK_AMOUNT:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_amount)],
            ASK_TAN:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tan)],
            ASK_EFF:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_eff)],
            ASK_TERM:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_term)],
            ASK_FEE:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_fee)],
            SDD_ADDR:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_addr)],
            SDD_CITY:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_city)],
            SDD_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_country)],
            SDD_ID:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_id)],
            SDD_IBAN:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_iban)],
            SDD_BIC:[MessageHandler(filters.TEXT & ~filters.COMMAND, sdd_bic)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    conv_aml = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_AML)), handle_menu)],
        states={
            ASK_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_country)],
            AML_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, aml_name)],
            AML_ID:[MessageHandler(filters.TEXT & ~filters.COMMAND, aml_id)],
            AML_IBAN:[MessageHandler(filters.TEXT & ~filters.COMMAND, aml_iban)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    conv_card = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_CARD)), handle_menu)],
        states={
            ASK_COUNTRY:[MessageHandler(filters.TEXT & ~filters.COMMAND, ask_country)],
            CARD_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND, card_name)],
            CARD_ADDR:[MessageHandler(filters.TEXT & ~filters.COMMAND, card_addr)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    conv_notary = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(re.escape(BTN_NOTARY)), handle_menu)],
        states={ASK_NOTARY_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, notary_amount)]},
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv_both)
    app.add_handler(conv_aml)
    app.add_handler(conv_card)
    app.add_handler(conv_notary)

    logging.info("GAFI DE/AT bot started (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
