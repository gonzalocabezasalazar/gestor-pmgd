import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.io as pio
import io
import json
import os
import gspread
import tempfile
from oauth2client.service_account import ServiceAccountCredentials
from datetime import timedelta
import numpy as np
from fpdf import FPDF

# Forzar tema plotly globalmente → ayuda con colores en exportación
pio.templates.default = "plotly"

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Monitor Planta Solar", layout="wide", initial_sidebar_state="expanded")

# --- UTILS FECHA ---
def obtener_nombre_mes(mes_num):
    meses = {1:"Enero", 2:"Febrero", 3:"Marzo", 4:"Abril", 5:"Mayo", 6:"Junio",
             7:"Julio", 8:"Agosto", 9:"Septiembre", 10:"Octubre", 11:"Noviembre", 12:"Diciembre"}
    return meses.get(mes_num, "")

# --- CONEXIÓN GOOGLE SHEETS ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "DB_FUSIBLES"

def conectar_google_sheets(hoja_nombre):
    creds = None
    if os.path.exists("credentials.json"):
        try: creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
        except: pass
    if creds is None:
        try:
            if "gcp_service_account" in st.secrets:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), SCOPE)
        except: pass
    if creds is None: 
        st.error("🚫 Error de Llaves.")
        st.stop()
    try:
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        try: return spreadsheet.worksheet(hoja_nombre)
        except: return spreadsheet.sheet1
    except Exception as e: 
        st.error(f"Error Conexión: {e}")
        st.stop()

# --- DATOS ---
@st.cache_data(ttl=300)
def cargar_datos_fusibles():
    sheet = conectar_google_sheets("Sheet1")
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        if 'Fecha' in df.columns: df['Fecha'] = pd.to_datetime(df['Fecha'])
        if 'Amperios' in df.columns: df['Amperios'] = pd.to_numeric(df['Amperios'], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=300)
def cargar_datos_mediciones():
    sheet = conectar_google_sheets("DB_MEDICIONES")
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=['Fecha', 'Planta', 'Equipo', 'String ID', 'Amperios'])
        df = pd.DataFrame(data)
        req = ['Fecha', 'Planta', 'Equipo', 'String ID', 'Amperios']
        for c in req:
            if c not in df.columns: df[c] = None
        if 'Fecha' in df.columns: df['Fecha'] = pd.to_datetime(df['Fecha'])
        if 'Amperios' in df.columns: df['Amperios'] = pd.to_numeric(df['Amperios'], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame(columns=['Fecha', 'Planta', 'Equipo', 'String ID', 'Amperios'])

def guardar_falla(registro):
    sheet = conectar_google_sheets("Sheet1")
    reg = [registro['Fecha'].strftime("%Y-%m-%d"), registro['Planta'], registro['Inversor'],
           registro['Caja'], registro['String'], registro['Polaridad'], str(registro['Amperios']), registro['Nota']]
    sheet.append_row(reg)
    st.cache_data.clear()

def borrar_registro(idx):
    try:
        sheet = conectar_google_sheets("Sheet1")
        sheet.delete_rows(idx + 2)
        st.cache_data.clear()
        st.session_state.df_cache = cargar_datos_fusibles()
        st.toast("Borrado OK")
    except: st.error("Error borrar")

def guardar_medicion_masiva(df_mediciones, planta, equipo, fecha):
    sheet = conectar_google_sheets("DB_MEDICIONES")
    filas = []
    f_str = fecha.strftime("%Y-%m-%d")
    for _, row in df_mediciones.iterrows():
        filas.append([f_str, planta, equipo, row['String ID'], row['Amperios']])
    sheet.append_rows(filas)
    st.cache_data.clear()
    st.toast("✅ Guardado")

# --- HELPERS ---
def crear_id_tecnico(row):
    try: 
        return f"{str(row['Inversor']).replace('Inv-','')}-{str(row['Caja']).replace('CB-','')}-{str(row['String']).replace('Str-','')} {'(+)' if 'Positivo' in str(row['Polaridad']) else '(-)'}"
    except: return "Error"

def generar_analisis_auto(df):
    if df.empty: return "Sin datos."
    total = len(df)
    eq = (df['Inversor'] + " > " + df['Caja']).mode()
    crit = eq[0] if not eq.empty else "N/A"
    pos = len(df[df['Polaridad'].astype(str).str.contains("Positivo")])
    neg = len(df[df['Polaridad'].astype(str).str.contains("Negativo")])
    trend = "Equilibrada"
    if pos > neg * 1.5: trend = "Predominancia positiva"
    if neg > pos * 1.5: trend = "Predominancia negativa"
    return f"Total de fallas: {total}. Equipo crítico: {crit}. Tendencia: {trend}. Promedio: {df['Amperios'].mean():.1f} A."

def generar_diagnostico_mediciones(df):
    vals = df['Amperios']
    prom = vals[vals > 0].mean() if not vals[vals > 0].empty else 0
    c0 = df[df['Amperios'] == 0]['String ID'].tolist()
    cb = df[(df['Amperios'] > 0) & (df['Amperios'] < prom * 0.90)]
    stt, col, det = "Normal", "success", "Parámetros dentro de rango esperado."
    if c0 or not cb.empty: 
        stt, col, det = "Anomalía detectada", "error", "Se detectaron valores fuera de rango o ceros."
    return stt, det, col, c0 + cb['String ID'].tolist()

# --- PDF ---
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'Informe Técnico PMGD', 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'C')

def clean_text_chile(text):
    if not isinstance(text, str):
        return str(text)
    
    # Reemplazos para mejorar presentación y compatibilidad con fpdf
    replacements = {
        '•': '-', '—': '-', '–': '-', '“': '"', '”': '"', '‘': "'", '’': "'",
        '⚡': '', 
        # Formas modernas aceptadas en Chile
        'sólo': 'solo', 'sóla': 'sola', 'sóles': 'soles', 'sólos': 'solos',
        'éste': 'este', 'ésta': 'esta', 'éstos': 'estos', 'éstas': 'estas',
        'guión': 'guion', 'Guión': 'Guion',
        # Corregir errores comunes de mayúsculas y tildes
        'Emision': 'Emisión', 'emision': 'emisión',
        'Tecnico': 'Técnico', 'tecnico': 'técnico',
        'Planta': 'Planta', 'Periodo': 'Período', 'periodo': 'período',
    }
    
    for k, v in replacements.items():
        text = text.replace(k, v)
    
    # Mantener tildes originales importantes
    text = text.replace('Ã¡', 'á').replace('Ã©', 'é').replace('Ã­', 'í').replace('Ã³', 'ó').replace('Ãº', 'ú')
    text = text.replace('Ã±', 'ñ').replace('Ã‘', 'Ñ')
    
    return text.encode('latin-1', 'replace').decode('latin-1')

def crear_pdf_gerencial(planta, periodo_texto, kpis, ia_text, engineer_text, fig_rank, fig_pie, fig_pol):
    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(True, margin=15)

    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, clean_text_chile(f"Reporte gerencial: {planta} – {periodo_texto}"), 0, 1, 'L')
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 10, clean_text_chile(f"Fecha de emisión: {pd.Timestamp.now().strftime('%d de %B de %Y')}"), 0, 1, 'L')
    pdf.ln(8)

    pdf.set_fill_color(230, 240, 255)
    pdf.rect(10, pdf.get_y(), 190, 20, 'F')
    pdf.set_font("Arial", "B", 10)
    pdf.cell(47, 10, "Fallas totales", 0, 0, 'C')
    pdf.cell(47, 10, "Equipo crítico", 0, 0, 'C')
    pdf.cell(47, 10, "Promedio (A)", 0, 0, 'C')
    pdf.cell(47, 10, "Máx. repeticiones", 0, 1, 'C')
    pdf.set_font("Arial", "", 12)
    pdf.cell(47, 10, str(kpis['total']), 0, 0, 'C')
    pdf.cell(47, 10, clean_text_chile(str(kpis['critico'])), 0, 0, 'C')
    pdf.cell(47, 10, str(kpis['promedio']), 0, 0, 'C')
    pdf.cell(47, 10, str(kpis['repes']), 0, 1, 'C')
    pdf.ln(12)

    pdf.set_font("Arial", "B", 11)
    pdf.cell(0, 8, "Diagnóstico automático", 0, 1)
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(0, 6, clean_text_chile(ia_text))
    pdf.ln(8)

    pdf.set_font("Arial", "B", 11)
    pdf.set_text_color(180, 0, 0)
    pdf.cell(0, 8, "Comentarios de gerencia", 0, 1)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(0, 6, clean_text_chile(engineer_text or "Sin comentarios ingresados."))
    pdf.ln(12)

    pdf.add_page()
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Anexo gráfico", 0, 1, 'C')
    pdf.ln(6)

    try:
        img_cfg = {"format": "png", "width": 900, "height": 550, "scale": 2.5}

        if fig_rank:
            fig_rank.update_layout(template="plotly", paper_bgcolor="white", plot_bgcolor="white")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1:
                fig_rank.write_image(t1.name, **img_cfg)
                pdf.image(t1.name, x=10, w=190)
                pdf.ln(6)

        y_pos = pdf.get_y()
        pie_cfg = {"format": "png", "width": 550, "height": 450, "scale": 2.5}

        if fig_pie:
            fig_pie.update_layout(template="plotly", paper_bgcolor="white", plot_bgcolor="white")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t2:
                fig_pie.write_image(t2.name, **pie_cfg)
                pdf.image(t2.name, x=10, y=y_pos, w=90)

        if fig_pol:
            fig_pol.update_layout(template="plotly", paper_bgcolor="white", plot_bgcolor="white")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t3:
                fig_pol.write_image(t3.name, **pie_cfg)
                pdf.image(t3.name, x=110, y=y_pos, w=90)

    except Exception as e:
        st.warning(f"Problema al generar gráficos en PDF: {e}")

    return bytes(pdf.output(dest='S'))

# ... (el resto del código sigue igual que la versión que te funcionó antes)

# Nota: solo copié hasta aquí para no repetir todo innecesariamente.
# Reemplaza las funciones PDF y clean_text por estas nuevas versiones.
# El resto (app, tabs, gráficos, etc.) mantenlo tal cual estaba en la versión que te dijo "quedó perfecto".
