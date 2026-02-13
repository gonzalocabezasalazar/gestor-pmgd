import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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

# Forzar tema plotly globalmente
pio.templates.default = "plotly"

# --- CONSTANTES DE NEGOCIO ---
VOLTAJE_DC = 1500
PRECIO_MWH = 40
HORAS_SOL_REP = 10
TOLERANCIA_CRITICA = 0.10  # 10%

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
    if creds is None: st.error("🚫 Error de Llaves."); st.stop()
    try: 
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        try: return spreadsheet.worksheet(hoja_nombre)
        except: return spreadsheet.sheet1
    except Exception as e: st.error(f"Error Conexión: {e}"); st.stop()

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
        if 'String_ID' in df.columns: df.rename(columns={'String_ID': 'String ID'}, inplace=True)
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
    except: st.error("Error al borrar")

def guardar_medicion_masiva(df_mediciones, planta, equipo, fecha):
    sheet = conectar_google_sheets("DB_MEDICIONES")
    filas = []
    f_str = fecha.strftime("%Y-%m-%d")
    for _, row in df_mediciones.iterrows():
        filas.append([f_str, planta, equipo, row['String ID'], row['Amperios']])
    sheet.append_rows(filas)
    st.cache_data.clear()
    st.session_state.df_med_cache = cargar_datos_mediciones()
    st.toast("✅ Guardado y Sincronizado")
    st.rerun()

# --- LOGICA DIAGNOSTICO ---
def clasificar_falla(amp):
    if amp < 4.0: return "Fatiga (<4A)"
    elif amp > 8.0: return "Sobrecarga (>8A)"
    else: return "Operativa (4-8A)"

def obtener_topologia(df_med, planta):
    if df_med.empty: return pd.DataFrame(columns=['Equipo', 'Strings Detectados', 'Origen'])
    df_p = df_med[df_med['Planta'] == planta]
    if df_p.empty: return pd.DataFrame(columns=['Equipo', 'Strings Detectados', 'Origen'])
    topo = df_p.groupby('Equipo')['String ID'].nunique().reset_index()
    topo.columns = ['Equipo', 'Strings Detectados']
    topo['Origen'] = 'Medición Real'
    return topo

# --- LOGICA ANALISIS MEDICIONES ---
def analizar_string_local(row):
    val = row['Amperios']
    ref_caja = row['Promedio_Caja']
    
    if val == 0: return "CORTE (0A)"
    if ref_caja == 0: return "NORMAL" 
    
    tolerancia = TOLERANCIA_CRITICA 
    
    if val < (ref_caja * (1 - tolerancia)): return "BAJA CORRIENTE"
    elif val > (ref_caja * (1 + tolerancia)): return "SOBRECORRIENTE"
    else: return "NORMAL"

def generar_diagnostico_mediciones_pro_local(df):
    if df.empty: return "NORMAL", "success", df
    df['Promedio_Caja'] = df.groupby('Equipo')['Amperios'].transform('mean')
    df['Diagnostico'] = df.apply(analizar_string_local, axis=1)
    df['Desviacion_Pct'] = np.where(df['Promedio_Caja'] > 0, 
                                    ((df['Amperios'] - df['Promedio_Caja']) / df['Promedio_Caja']) * 100, 
                                    0)
    return "OK", "success", df

# --- NUEVO: MOTOR DE NARRATIVA INTELIGENTE ---
def generar_narrativa_ia(df, planta):
    # Calcular estadisticas
    total_str = len(df)
    prom_global = df['Amperios'].mean()
    cortes = len(df[df['Diagnostico'] == "CORTE (0A)"])
    bajos = len(df[df['Diagnostico'] == "BAJA CORRIENTE"])
    
    # Encontrar mejor y peor caja
    df_cajas = df.groupby('Equipo')['Amperios'].mean().reset_index()
    mejor_caja = df_cajas.loc[df_cajas['Amperios'].idxmax()]
    peor_caja = df_cajas.loc[df_cajas['Amperios'].idxmin()]
    
    # Construir el texto
    texto = (
        f"Durante la inspeccion tecnica realizada en la planta {planta}, se evaluaron {total_str} strings. "
        f"El rendimiento promedio global fue de {prom_global:.1f} Amperios. "
    )
    
    if cortes == 0 and bajos == 0:
        texto += "La planta opera en condiciones optimas, sin deteccion de anomalias criticas. "
    else:
        texto += f"Se detectaron oportunidades de mejora: {cortes} strings sin generacion (Cortes) y {bajos} strings con bajo rendimiento operativo. "
    
    texto += (
        f"Analizando la consistencia por equipos, la caja {mejor_caja['Equipo']} presento el mejor desempeño "
        f"({mejor_caja['Amperios']:.1f}A), mientras que la unidad {peor_caja['Equipo']} registro el promedio mas bajo "
        f"({peor_caja['Amperios']:.1f}A), sugiriendo una posible necesidad de limpieza o revision focalizada en ese sector."
    )
    
    return texto

# --- HELPERS ---
def crear_id_tecnico(row):
    try: return f"{str(row['Inversor']).replace('Inv-','')}-{str(row['Caja']).replace('CB-','')}-{str(row['String']).replace('Str-','')} {'(+)' if 'Positivo' in str(row['Polaridad']) else '(-)'}"
    except: return "Error"

def generar_analisis_auto(df, perdida_total):
    return f"Resumen Ejecutivo:\n- Pérdida Económica Est: {perdida_total} USD."

# --- PDF MASTER (V43 - BRANDING MUNDO SOLAR) ---
class PDF(FPDF):
    def header(self):
        # 1. Branding Corporativo (Derecha, pequeño)
        self.set_font('Arial', 'B', 8)
        self.set_text_color(100, 100, 100) # Gris
        self.cell(0, 5, 'Elaborado por Equipo Tecnico MUNDO SOLAR SpA', 0, 1, 'R')
        
        # 2. Titulo Principal (Centro, Grande)
        self.set_font('Arial', 'B', 14)
        self.set_text_color(0, 0, 0) # Negro
        self.cell(0, 10, 'INFORME TECNICO PMGD - AUDITORIA DC', 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8); self.cell(0, 10, f'Pagina {self.page_no()} - Mundo Solar SpA', 0, 0, 'C')

def clean_text(text):
    if not isinstance(text, str): return str(text)
    replacements = {'•':'-', '—':'-', '–':'-', '“':'"', '”':'"', '‘':"'", '’':"'", '⚡':''}
    for k, v in replacements.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'replace').decode('latin-1')

def generar_reporte_completo_pdf(planta, df_mediciones):
    pdf = PDF()
    
    # Procesar datos
    stt_glob, col_glob, df_proc = generar_diagnostico_mediciones_pro_local(df_mediciones)
    df_proc['Equipo'] = df_proc['Equipo'].astype(str)
    
    # Generar Narrativa Automatica (IA)
    narrativa = generar_narrativa_ia(df_proc, planta)
    
    # === PAGINA 1: RESUMEN Y BOXPLOT ===
    pdf.add_page()
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"1. Analisis Ejecutivo de Performance"), 0, 1, 'L')
    
    # Insertar Narrativa Automatica
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(0, 6, clean_text(narrativa))
    pdf.ln(5)
    
    # Boxplot (Distribución por Caja)
    fig_box = px.box(df_proc, x='Equipo', y='Amperios', title="Distribución de Corriente por Combiner Box")
    fig_box.update_layout(template="plotly_white", width=800, height=400)
    
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1:
        fig_box.write_image(t1.name, scale=2)
        pdf.image(t1.name, x=10, w=190)
    pdf.ln(5)
    pdf.set_font("Arial", "I", 9)
    pdf.multi_cell(0, 6, clean_text("Nota: El grafico superior muestra la variabilidad interna. Cajas con rangos amplios indican inconsistencia (posibles sombras parciales)."))

    # === PAGINA 2: RANKING PROMEDIOS ===
    pdf.add_page()
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text("2. Ranking de Rendimiento por Caja"), 0, 1, 'L')
    
    df_mean = df_proc.groupby('Equipo')['Amperios'].mean().reset_index().sort_values('Amperios', ascending=False)
    fig_bar = px.bar(df_mean, x='Equipo', y='Amperios', title="Promedio de Corriente (Mayor a Menor)", color='Amperios', color_continuous_scale='Viridis')
    fig_bar.update_layout(template="plotly_white", width=800, height=400)
    
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t2:
        fig_bar.write_image(t2.name, scale=2)
        pdf.image(t2.name, x=10, w=190)
    
    # === PAGINA 3: DISTRIBUCION Y MAPA ===
    pdf.add_page()
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text("3. Mapa de Calor y Distribución"), 0, 1, 'L')
    
    # Histograma
    fig_hist = px.histogram(df_proc, x="Amperios", nbins=20, title="Curva de Distribución (Gauss)", marginal="box")
    fig_hist.update_layout(template="plotly_white", width=800, height=300)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t3:
        fig_hist.write_image(t3.name, scale=2)
        pdf.image(t3.name, x=10, w=180)
    
    pdf.ln(5)
    
    # Scatter Map (Mapa de Strings)
    df_proc = df_proc.sort_values(['Equipo', 'String ID'])
    df_proc['Indice'] = range(len(df_proc))
    
    fig_scat = px.scatter(df_proc, x='Indice', y='Amperios', color='Amperios', 
                          title="Mapa de Dispersión (Vista Panorámica)", color_continuous_scale='RdYlGn')
    fig_scat.update_layout(template="plotly_white", width=800, height=300)
    
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t4:
        fig_scat.write_image(t4.name, scale=2)
        pdf.image(t4.name, x=10, w=180)

    # === PAGINA 4: HALLAZGOS ===
    pdf.add_page()
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"4. Hallazgos Críticos (Desviación > {int(TOLERANCIA_CRITICA*100)}%)"), 0, 1, 'L')
    pdf.ln(5)
    
    filtro_problemas = (df_proc['Diagnostico'] == "BAJA CORRIENTE") | (df_proc['Diagnostico'] == "CORTE (0A)")
    df_hallazgos = df_proc[filtro_problemas].sort_values('Desviacion_Pct', ascending=True)
    
    if not df_hallazgos.empty:
        pdf.set_font("Arial", "B", 10)
        pdf.set_fill_color(240, 240, 240)
        headers = ["Equipo", "String ID", "Valor (A)", "Prom. Caja", "Desviacion"]
        w_cols = [40, 30, 30, 30, 40]
        
        for i, h in enumerate(headers):
            pdf.cell(w_cols[i], 8, clean_text(h), 1, 0, 'C', True)
        pdf.ln()
        
        pdf.set_font("Arial", "", 10)
        for _, row in df_hallazgos.iterrows():
            pdf.cell(w_cols[0], 8, clean_text(str(row['Equipo'])), 1)
            pdf.cell(w_cols[1], 8, clean_text(str(row['String ID'])), 1)
            pdf.cell(w_cols[2], 8, f"{row['Amperios']:.1f}", 1, 0, 'C')
            pdf.cell(w_cols[3], 8, f"{row['Promedio_Caja']:.1f}", 1, 0, 'C')
            pdf.set_text_color(200, 0, 0)
            pdf.cell(w_cols[4], 8, f"{row['Desviacion_Pct']:.1f}%", 1, 0, 'C')
            pdf.set_text_color(0)
            pdf.ln()
    else:
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, clean_text("No se detectaron desviaciones criticas en esta inspeccion."))
        
    return bytes(pdf.output(dest='S'))


def crear_pdf_mediciones_caja(planta, equipo, fecha, df_data, kpis, comentarios, fig_box, evidencias):
    pdf = PDF(); pdf.add_page(); pdf.set_auto_page_break(True, margin=15)
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"REPORTE DE CAMPO (SIMPLE)"), 0, 1, 'C'); pdf.ln(5)
    pdf.set_font("Arial", "", 10); pdf.cell(0, 8, clean_text(f"Planta: {planta} | Equipo: {equipo}"), 0, 1); pdf.cell(0, 8, clean_text(f"Fecha: {fecha}"), 0, 1); pdf.ln(5)
    
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1: 
            fig_box.write_image(t1.name, format="png", width=900, height=450, scale=2)
            pdf.image(t1.name, x=10, w=190)
    except: pass
    pdf.ln(5); pdf.set_font("Arial", "B", 10); pdf.cell(30, 8, "String", 1, 0, 'C'); pdf.cell(30, 8, "Valor", 1, 0, 'C'); pdf.cell(80, 8, "Estado", 1, 1, 'C'); pdf.set_font("Arial", "", 10)
    for _, r in df_data.iterrows():
        pdf.cell(30, 8, clean_text(str(r['String ID'])), 1, 0, 'C'); pdf.cell(30, 8, f"{r['Amperios']:.1f} A", 1, 0, 'C')
        pdf.cell(80, 8, clean_text(r['Diagnostico']), 1, 1, 'C')
    return bytes(pdf.output(dest='S'))

def generar_excel_maestro(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as w: df.to_excel(w, index=False)
    return output.getvalue()

def generar_excel_pro(df_reporte, planta, periodo, comentarios):
    output = io.BytesIO()
    if df_reporte.empty: return None
    df_rep = df_reporte.copy()
    df_rep['ID_Tecnico'] = df_rep.apply(crear_id_tecnico, axis=1)
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb = writer.book
            ws = wb.add_worksheet('Reporte Ingeniería')
            ws.hide_gridlines(2)
            f_title = wb.add_format({'bold': True, 'font_size': 16, 'color': 'white', 'bg_color': '#2e86c1', 'align': 'center'})
            f_wrap = wb.add_format({'text_wrap': True, 'border': 1, 'valign': 'top'})
            ws.merge_range('B2:H2', f"INFORME TECNICO: {planta.upper()}", f_title)
            ws.write('B3', f"Periodo: {periodo}")
            ws.merge_range('B6:F12', comentarios, f_wrap)
            df_export = df_rep[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']]
            df_export['Fecha'] = df_export['Fecha'].dt.date
            df_export.to_excel(writer, sheet_name='Reporte Ingeniería', startrow=15, startcol=1, index=False)
    except: return None
    return output.getvalue()

# --- APP ---
if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos_fusibles()
if 'df_med_cache' not in st.session_state: st.session_state.df_med_cache = cargar_datos_mediciones()

PLANTAS_DEF = ["El Roble", "Las Rojas"]
def cargar_plantas():
    try: return json.load(open("plantas_config.json"))
    except: return PLANTAS_DEF
plantas = cargar_plantas()

st.title("⚡ Monitor Planta Solar")
if st.button("🔄 Sincronizar"): 
    st.session_state.df_cache = cargar_datos_fusibles()
    st.session_state.df_med_cache = cargar_datos_mediciones()
    st.rerun()

with st.sidebar:
    st.header("Configuración")
    planta_sel = st.selectbox("Planta:", plantas)
    with st.expander("Admin"):
        nuevo = st.text_input("Agregar planta")
        if st.button("Agregar") and nuevo: plantas.append(nuevo); json.dump(plantas, open("plantas_config.json", 'w')); st.rerun()

t1, t2, t3, t4 = st.tabs(["📝 Fallas", "⚡ Mediciones", "📊 Informes", "🔍 Diagnóstico"])

with t1:
    st.subheader(f"Registro: {planta_sel}")
    with st.form("f1"):
        c1, c2, c3, c4 = st.columns(4)
        f = c1.date_input("Fecha")
        i = c2.number_input("Inv", 1, 50)
        c = c3.number_input("Caja", 1, 100)
        s = c4.number_input("Str", 1, 30)
        c5, c6, c7 = st.columns(3)
        p = c5.selectbox("Pol", ["Positivo (+)", "Negativo (-)"])
        a = c6.number_input("A", 0.0, 30.0)
        n = c7.text_input("Nota")
        if st.form_submit_button("Guardar"):
            guardar_falla({'Fecha': pd.to_datetime(f), 'Planta': planta_sel, 'Inversor': f"Inv-{i}", 'Caja': f"CB-{c}", 'String': f"Str-{s}", 'Polaridad': p, 'Amperios': a, 'Nota': n})
            st.session_state.df_cache = cargar_datos_fusibles()
            st.rerun()
    df_s = st.session_state.df_cache[st.session_state.df_cache['Planta'] == planta_sel]
    if not df_s.empty:
        for idx, r in df_s.tail(5).sort_index(ascending=False).iterrows():
            cols = st.columns([1, 2, 2, 1, 1, 1])
            cols[0].write(r['Fecha'].strftime('%d/%m'))
            cols[1].write(f"{r['Inversor']}>{r['Caja']}")
            cols[2].write(crear_id_tecnico(r))
            cols[3].write(f"{r['Amperios']}A")
            cols[4].caption(r['Nota'])
            if cols[5].button("🗑️", key=f"d{idx}"): borrar_registro(idx); st.rerun()

with t2:
    st.subheader("Mediciones")
    c1, c2, c3 = st.columns(3)
    mi = c1.number_input("Inv", 1, 50, key="mi")
    mc = c2.number_input("Caja", 1, 100, key="mc")
    ns = c3.number_input("Cant", 4, 32, 12)
    mf = c3.date_input("Fecha", key="mf")
    
    if 'data_med' not in st.session_state or len(st.session_state['data_med']) != ns:
        st.session_state['data_med'] = pd.DataFrame({'String ID': [f"Str-{i+1}" for i in range(ns)], 'Amperios': [0.0] * ns})
    
    ce, cs = st.columns([1, 1])
    df_ed = ce.data_editor(st.session_state['data_med'], height=(35 * ns) + 40, hide_index=True)
    vals = df_ed['Amperios']
    v_cl = vals[vals > 0]
    
    if not v_cl.empty:
        prom = v_cl.mean(); dev = v_cl.std(); cv = (dev / prom) * 100 if prom > 0 else 0
        cs.metric("Promedio Local", f"{prom:.2f} A")
        
        def diag_simple(v, p):
            if v == 0: return "CORTE (0A)"
            if v < p * (1 - TOLERANCIA_CRITICA): return "BAJA CORRIENTE"
            return "NORMAL"
            
        df_ed['Diagnostico'] = df_ed['Amperios'].apply(lambda x: diag_simple(x, prom))
        
        fig = px.bar(df_ed, x='String ID', y='Amperios', color='Diagnostico', 
                     color_discrete_map={'NORMAL': '#2ecc71', 'CORTE (0A)': '#e74c3c', 'BAJA CORRIENTE': '#f39c12'})
        fig.update_layout(template="plotly", paper_bgcolor='white', plot_bgcolor='white', margin=dict(l=10, r=10, t=40, b=40), height=400)
        fig.add_hline(y=prom, line_dash="dash", line_color="gray")
        cs.plotly_chart(fig, use_container_width=True)
        
        st.divider(); comm = st.text_area("Notas:"); imgs = st.file_uploader("Fotos", accept_multiple_files=True)
        cb1, cb2 = st.columns(2)
        if cb1.button("💾 Guardar"): guardar_medicion_masiva(df_ed, planta_sel, f"Inv-{mi}>CB-{mc}", mf)
        kpis = {'promedio': f"{prom:.1f}", 'dispersion': f"{cv:.1f}%", 'estado': "Carga Manual"}
        
        cb2.download_button("📄 PDF Caja", crear_pdf_mediciones_caja(planta_sel, f"Inv-{mi}>CB-{mc}", mf.strftime("%d-%m-%Y"), df_ed, kpis, comm, fig, imgs), f"Med_{mc}.pdf")

with t3:
    st.header("Informes")
    mode = st.radio("Tipo:", ["Fallas", "Mediciones"], horizontal=True); st.divider()
    
    if mode == "Fallas":
        # (Codigo Fallas sin cambios)
        df = st.session_state.df_cache; df_f = df[df['Planta'] == planta_sel].copy()
        if not df_f.empty:
            df_f['Equipo_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
            c_f, c_k = st.columns([1, 3])
            with c_f:
                st.markdown("⏱️ **Filtros**")
                filtro_t = st.radio("Periodo:", ["Todo", "Este Mes", "Último Trimestre", "Último Semestre", "Último Año", "Mes Específico"])
                hoy = pd.Timestamp.now(); fecha_texto = "Histórico Completo"
                if filtro_t == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]; fecha_texto = f"{obtener_nombre_mes(hoy.month)} {hoy.year}"
                elif filtro_t == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]; fecha_texto = "Últimos 90 Días"
                elif filtro_t == "Último Semestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=180))]; fecha_texto = "Últimos 180 Días"
                elif filtro_t == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]; fecha_texto = "Último Año"
                elif filtro_t == "Mes Específico":
                    mm = st.selectbox("Mes", range(1, 13), index=hoy.month - 1, format_func=obtener_nombre_mes)
                    aa = st.number_input("Año", 2023, 2030, hoy.year)
                    df_f = df_f[(df_f['Fecha'].dt.month == mm) & (df_f['Fecha'].dt.year == aa)]; fecha_texto = f"{obtener_nombre_mes(mm)} {aa}"
            with c_k:
                repes = 0; critico = "-"; df_f['Perdida'] = df_f['Amperios'] * (VOLTAJE_DC * PRECIO_MWH * HORAS_SOL_REP / 1000000); perdida_total = f"{df_f['Perdida'].sum():.1f} USD"
                if not df_f.empty: conteos = df_f['Equipo_Full'].value_counts(); critico = conteos.idxmax(); repes = conteos.max()
                kpis = {'total': len(df_f), 'promedio': f"{df_f['Amperios'].mean():.1f} A", 'critico': critico, 'repes': repes, 'perdida': perdida_total}
                k1, k2, k3, k4 = st.columns([1, 1, 1.5, 1])
                k1.metric("Fallas", kpis['total']); k2.metric("Promedio", kpis['promedio']); k3.metric("Equipo Crítico", kpis['critico']); k4.metric("Perdida Est.", kpis['perdida'])
            st.subheader("Análisis Visual")
            df_heat = df_f.groupby(['Inversor', 'Caja']).size().reset_index(name='Fallas')
            fig_heat = px.density_heatmap(df_heat, x='Inversor', y='Caja', z='Fallas', title="Mapa de Calor (Concentración)", color_continuous_scale='Reds')
            fig_heat.update_layout(template="plotly", paper_bgcolor='white', plot_bgcolor='white', height=400); st.plotly_chart(fig_heat, use_container_width=True)
            c1, c2, c3 = st.columns(3); l_cfg = dict(margin=dict(l=10, r=10, t=50, b=10), height=350, paper_bgcolor='white', plot_bgcolor='white')
            drk = df_f.groupby('Equipo_Full').agg(Fallas=('Fecha', 'count')).reset_index().sort_values('Fallas', ascending=True)
            frk = px.bar(drk, x='Fallas', y='Equipo_Full', orientation='h', title="Ranking"); frk.update_layout(**l_cfg); c1.plotly_chart(frk, use_container_width=True)
            fpi = px.pie(df_f, names='Inversor', title="Inversores"); fpi.update_layout(**l_cfg); c2.plotly_chart(fpi, use_container_width=True)
            fpo = px.pie(df_f, names='Polaridad', title="Polaridad", color_discrete_sequence=['#e74c3c', '#3498db'], hole=0.4); fpo.update_traces(textinfo='percent+label', textposition='inside'); fpo.update_layout(showlegend=True, height=380, paper_bgcolor='white', plot_bgcolor='white'); c3.plotly_chart(fpo, use_container_width=True)
            ia = generar_analisis_auto(df_f, perdida_total); st.info(ia); txt = st.text_area("Conclusiones:")
            c_pdf, c_xls = st.columns(2)
            with c_xls: excel_data = generar_excel_pro(df_f, planta_sel, fecha_texto, txt); 
            if excel_data: st.download_button("📥 Excel Datos", excel_data, f"Datos_Fallas_{planta_sel}.xlsx")
        else: st.info("Sin datos.")

    else:
        # --- SECCIÓN MEDICIONES V43 ---
        dfm = st.session_state.df_med_cache; dfmp = dfm[dfm['Planta'] == planta_sel].copy()
        
        if not dfmp.empty:
            stt_glob, col_glob, df_processed = generar_diagnostico_mediciones_pro_local(dfmp)
            
            st.markdown("### 🚦 Resumen Ejecutivo (Audit Master)")
            c_kpi, c_btn = st.columns([3, 1])
            with c_kpi: st.caption(f"Tolerancia Crítica Aplicada: {int(TOLERANCIA_CRITICA*100)}%")
            with c_btn:
                pdf_bytes = generar_reporte_completo_pdf(planta_sel, dfmp)
                st.download_button("📄 GENERAR INFORME PDF", pdf_bytes, f"Informe_Auditoria_{planta_sel}.pdf", type="primary")

            tot_strings = len(df_processed)
            tot_criticos = len(df_processed[df_processed['Diagnostico'] == "CORTE (0A)"])
            tot_bajos = len(df_processed[df_processed['Diagnostico'] == "BAJA CORRIENTE"])
            salud = ((tot_strings - tot_criticos - tot_bajos) / tot_strings) * 100
            
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Strings Medidos", tot_strings)
            k2.metric("Salud Planta", f"{salud:.1f}%")
            k3.metric("Cortes (0A)", tot_criticos, delta_color="inverse")
            k4.metric("Desviación > 10%", tot_bajos, delta_color="inverse")
            
            st.divider()
            
            st.subheader("1. Dispersión por Caja (Boxplot)")
            fig_box = px.box(df_processed, x='Equipo', y='Amperios', points="outliers", color="Equipo", title="Distribución de Corriente por Combiner Box")
            fig_box.update_layout(showlegend=False, height=450)
            st.plotly_chart(fig_box, use_container_width=True)
            
            c_hist, c_scat = st.columns(2)
            with c_hist:
                st.subheader("2. Histograma (Gauss)")
                fig_hist = px.histogram(df_processed, x="Amperios", nbins=20, title="Distribución de Frecuencias", marginal="box")
                st.plotly_chart(fig_hist, use_container_width=True)
                
            with c_scat:
                st.subheader("3. Mapa de Strings")
                df_processed = df_processed.sort_values(['Equipo', 'String ID'])
                df_processed['Indice'] = range(len(df_processed))
                
                fig_scat = px.scatter(df_processed, 
                                      x='Indice', 
                                      y='Amperios', 
                                      color='Amperios', 
                                      title="Mapa de Dispersión (Ordenado)", 
                                      color_continuous_scale='RdYlGn',
                                      hover_data=['Equipo', 'String ID'])
                st.plotly_chart(fig_scat, use_container_width=True)

            st.subheader("4. Top Strings con Desviación")
            filtro_problemas = (df_processed['Diagnostico'] == "BAJA CORRIENTE") | (df_processed['Diagnostico'] == "CORTE (0A)")
            df_bajos = df_processed[filtro_problemas].sort_values('Amperios', ascending=True).head(15)
            if not df_bajos.empty:
                df_bajos['ID_Full'] = df_bajos['Equipo'] + " : " + df_bajos['String ID']
                fig_bar = px.bar(df_bajos, x='Amperios', y='ID_Full', orientation='h', color='Diagnostico', color_discrete_map={'CORTE (0A)': '#e74c3c', 'BAJA CORRIENTE': '#f39c12'})
                st.plotly_chart(fig_bar, use_container_width=True)
            else: st.success("Sin anomalías graves.")

            with st.expander("Ver Base de Datos Completa"):
                st.dataframe(df_processed, use_container_width=True)
        else:
            st.warning("Sin mediciones registradas.")

with t4:
    st.header("🔍 Diagnóstico Técnico Avanzado")
    df = st.session_state.df_cache; df_d = df[df['Planta'] == planta_sel].copy()
    if not df_d.empty:
        c_gh, c_typ = st.columns(2)
        with c_gh:
            st.subheader("👻 Strings Fantasma")
            df_d['ID_Unico'] = df_d['Inversor'] + " > " + df_d['Caja'] + " > " + df_d['String']
            counts = df_d['ID_Unico'].value_counts(); ghosts = counts[counts > 1]
            if not ghosts.empty: st.error(f"Se detectaron {len(ghosts)} strings con fallas múltiples."); st.dataframe(ghosts.rename("Fallas"), use_container_width=True)
            else: st.success("No hay strings reincidentes.")
        with c_typ:
            st.subheader("⚡ Clasificación de Causa")
            df_d['Tipo_Falla'] = df_d['Amperios'].apply(clasificar_falla)
            fig_pie_type = px.pie(df_d, names='Tipo_Falla', title="Fatiga vs Sobrecarga", color='Tipo_Falla', color_discrete_map={"Fatiga (<4A)": "#2ecc71", "Sobrecarga (>8A)": "#e74c3c", "Operativa (4-8A)": "#f1c40f"}, hole=0.6)
            st.plotly_chart(fig_pie_type, use_container_width=True)
    else: st.info("Sin datos de fallas.")
    st.divider()
    st.subheader("🗺️ Monitor de Topología")
    df_meds = st.session_state.df_med_cache; topo_data = obtener_topologia(df_meds, planta_sel)
    if not topo_data.empty: st.dataframe(topo_data, use_container_width=True)
    else: st.warning("No hay mediciones registradas.")
