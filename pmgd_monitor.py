import streamlit as st
import pandas as pd
import plotly.express as px
import io
import json
import os
import gspread
import tempfile
from oauth2client.service_account import ServiceAccountCredentials
from datetime import timedelta
import numpy as np
from fpdf import FPDF

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor PMGD Pro", layout="wide", initial_sidebar_state="expanded")

# --- CONEXIÓN MULTI-HOJA ---
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

# --- GESTIÓN DE DATOS ---
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

def cargar_datos_mediciones():
    sheet = conectar_google_sheets("DB_MEDICIONES")
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame()
        df = pd.DataFrame(data)
        if 'Fecha' in df.columns: df['Fecha'] = pd.to_datetime(df['Fecha'])
        if 'Amperios' in df.columns: df['Amperios'] = pd.to_numeric(df['Amperios'], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame()

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
        st.toast("Borrado OK", icon="🗑️")
    except: st.error("Error borrar")

def guardar_medicion_masiva(df_mediciones, planta, equipo, fecha):
    sheet = conectar_google_sheets("DB_MEDICIONES")
    filas_para_subir = []
    fecha_str = fecha.strftime("%Y-%m-%d")
    for idx, row in df_mediciones.iterrows():
        fila = [fecha_str, planta, equipo, row['String ID'], row['Amperios']]
        filas_para_subir.append(fila)
    sheet.append_rows(filas_para_subir)
    st.cache_data.clear()
    st.toast(f"✅ Guardados {len(filas_para_subir)} strings correctamente")

# --- MOTORES DE INTELIGENCIA ---
def crear_id_tecnico(row):
    try:
        i = str(row['Inversor']).replace('Inv-', '')
        c = str(row['Caja']).replace('CB-', '')
        s = str(row['String']).replace('Str-', '')
        p = "(+)" if "Positivo" in str(row['Polaridad']) else "(-)"
        return f"{i}-{c}-{s} {p}"
    except: return "Error"

def generar_analisis_auto(df):
    if df.empty: return "Sin datos."
    total = len(df)
    eq_mode = (df['Inversor'] + " > " + df['Caja']).mode()
    critico = eq_mode[0] if not eq_mode.empty else "N/A"
    pos = len(df[df['Polaridad'].astype(str).str.contains("Positivo")])
    neg = len(df[df['Polaridad'].astype(str).str.contains("Negativo")])
    trend = "Equilibrada"
    if pos > neg * 1.5: trend = "PREDOMINANCIA POSITIVA"
    if neg > pos * 1.5: trend = "PREDOMINANCIA NEGATIVA"
    promedio_amp = df['Amperios'].mean()
    return (f"Resumen Ejecutivo:\n"
            f"- Volumen de Fallas: {total} eventos registrados.\n"
            f"- Equipo Critico: {critico} (Mayor recurrencia).\n"
            f"- Tendencia Polaridad: {trend}.\n"
            f"- Intensidad Promedio: {promedio_amp:.1f} Amperios.")

def generar_diagnostico_mediciones(df):
    vals = df['Amperios']
    promedio = vals[vals > 0].mean() if not vals[vals > 0].empty else 0
    cuerdas_cero = df[df['Amperios'] == 0]['String ID'].tolist()
    cuerdas_bajas = df[(df['Amperios'] > 0) & (df['Amperios'] < promedio * 0.90)]
    
    estado = "NORMAL"
    detalle = "La caja opera dentro de parametros normales."
    color_msg = "success"
    
    if cuerdas_cero or not cuerdas_bajas.empty:
        estado = "ANOMALIA DETECTADA"
        color_msg = "error"
        detalle = "DIAGNOSTICO TECNICO:\n"
        if cuerdas_cero:
            detalle += f"- Strings MUERTOS (0A): {', '.join(cuerdas_cero)}. Causa: Fusible/Cable.\n"
        if not cuerdas_bajas.empty:
            detalle += f"- Desbalance Detectado (Posible PID/Diodos/Suciedad)."
            
    return estado, detalle, color_msg, cuerdas_cero + cuerdas_bajas['String ID'].tolist()

# --- GENERADOR DE PDF BLINDADO ---
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(0, 10, 'INFORME TECNICO PMGD', 0, 1, 'C')
        self.ln(5)
    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

def clean_text(text):
    """Limpia caracteres incompatibles con PDF standard (Latin-1)"""
    if not isinstance(text, str): return str(text)
    replacements = {
        '•': '-', '—': '-', '–': '-', '“': '"', '”': '"', 
        '‘': "'", '’': "'", 'ñ': 'n', 'Ñ': 'N', 'á': 'a', 
        'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'Á': 'A', 
        'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U', '⚡': 'Energia: '
    }
    for k, v in replacements.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'ignore').decode('latin-1')

def crear_pdf_gerencial(planta, periodo, kpis, ia_text, engineer_text, fig_rank, fig_pie):
    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"Reporte Gerencial: {planta} | {periodo}"), 0, 1, 'L')
    pdf.set_font("Arial", "", 10); pdf.cell(0, 10, clean_text(f"Fecha: {pd.Timestamp.now().strftime('%d-%m-%Y')}"), 0, 1, 'L'); pdf.ln(5)
    
    pdf.set_fill_color(230, 240, 255); pdf.rect(10, pdf.get_y(), 190, 25, 'F')
    pdf.set_font("Arial", "B", 11); pdf.cell(45, 10, "Total Fallas", 0, 0, 'C'); pdf.cell(45, 10, "Equipo Critico", 0, 0, 'C'); pdf.cell(45, 10, "Promedio (A)", 0, 0, 'C'); pdf.cell(45, 10, "Repeticiones", 0, 1, 'C')
    pdf.set_font("Arial", "", 12); pdf.cell(45, 10, str(kpis['total']), 0, 0, 'C'); pdf.cell(45, 10, clean_text(str(kpis['critico'])), 0, 0, 'C'); pdf.cell(45, 10, str(kpis['promedio']), 0, 0, 'C'); pdf.cell(45, 10, str(kpis['repes']), 0, 1, 'C'); pdf.ln(10)
    
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text("Diagnostico Automatico (IA)"), 0, 1, 'L')
    pdf.set_font("Arial", "", 10); pdf.multi_cell(0, 7, clean_text(ia_text)); pdf.ln(5)
    
    pdf.set_font("Arial", "B", 12); pdf.set_text_color(200, 0, 0); pdf.cell(0, 10, clean_text("Conclusiones Gerencia"), 0, 1, 'L')
    pdf.set_text_color(0, 0, 0); pdf.set_font("Arial", "", 10); pdf.multi_cell(0, 7, clean_text(engineer_text) if engineer_text else "Sin observaciones."); pdf.ln(10)
    
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1: fig_rank.write_image(t1.name, width=600, height=350); pdf.image(t1.name, x=10, w=180)
        pdf.ln(5)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t2: fig_pie.write_image(t2.name, width=400, height=300); pdf.image(t2.name, x=50, w=100)
    except: pass
    
    return bytes(pdf.output())

def crear_pdf_mediciones(planta, equipo, fecha, df_data, kpis, comentarios_gerencia, fig_box, evidencias):
    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"REPORTE DE MEDICION DE CAMPO (Curvas I-V)"), 0, 1, 'C'); pdf.ln(5)
    pdf.set_font("Arial", "", 10); pdf.cell(0, 8, clean_text(f"Planta: {planta}"), 0, 1); pdf.cell(0, 8, clean_text(f"Equipo Auditado: {equipo}"), 0, 1); pdf.cell(0, 8, clean_text(f"Fecha Medicion: {fecha}"), 0, 1); pdf.ln(5)

    pdf.set_fill_color(240, 240, 240); pdf.rect(10, pdf.get_y(), 190, 20, 'F')
    pdf.set_font("Arial", "B", 11); pdf.cell(63, 10, "Promedio Caja", 0, 0, 'C'); pdf.cell(63, 10, "Dispersion (%)", 0, 0, 'C'); pdf.cell(63, 10, "Estado Global", 0, 1, 'C')
    pdf.set_font("Arial", "", 12); pdf.cell(63, 10, f"{kpis['promedio']}", 0, 0, 'C')
    if float(kpis['dispersion'].replace('%','')) > 5: pdf.set_text_color(200, 0, 0)
    pdf.cell(63, 10, f"{kpis['dispersion']}", 0, 0, 'C'); pdf.set_text_color(0,0,0); pdf.cell(63, 10, clean_text(kpis['estado']), 0, 1, 'C'); pdf.ln(10)

    pdf.set_font("Arial", "B", 12); pdf.set_text_color(0, 50, 150); pdf.cell(0, 10, clean_text("Informe a Gerencia"), 0, 1, 'L')
    pdf.set_text_color(0, 0, 0); pdf.set_font("Arial", "", 10); pdf.multi_cell(0, 6, clean_text(comentarios_gerencia) if comentarios_gerencia else "Sin observaciones."); pdf.ln(10)

    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1: fig_box.write_image(t1.name, width=700, height=350); pdf.image(t1.name, x=10, w=190)
    except: pass
    pdf.ln(5)

    pdf.set_font("Arial", "B", 10); pdf.cell(40, 8, "String ID", 1, 0, 'C', True); pdf.cell(40, 8, "Corriente (A)", 1, 0, 'C', True); pdf.cell(60, 8, "Estado", 1, 1, 'C', True); pdf.set_font("Arial", "", 10)
    
    # CORRECCIÓN DE "FALSE" FANTASMA
    for index, row in df_data.iterrows():
        pdf.cell(40, 8, clean_text(str(row['String ID'])), 1, 0, 'C')
        pdf.cell(40, 8, f"{row['Amperios']:.1f} A", 1, 0, 'C')
        
        estado_str = "OK"
        if row['Estado'] == 'CRÍTICO': 
            pdf.set_text_color(200, 0, 0)
            estado_str = "CRITICO"
        else: pdf.set_text_color(0, 100, 0)
        
        pdf.cell(60, 8, estado_str, 1, 1, 'C')
        pdf.set_text_color(0, 0, 0)

    if evidencias:
        pdf.add_page(); pdf.set_font("Arial", "B", 14); pdf.cell(0, 10, clean_text("ANEXO FOTOGRAFICO"), 0, 1, 'C'); pdf.ln(10)
        for img_file in evidencias:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tf: tf.write(img_file.getbuffer()); pdf.image(tf.name, w=170); pdf.ln(5)
            except: pass
            
    return bytes(pdf.output())

# 3. EXCEL MAESTRO
def generar_excel_maestro_mediciones(df, planta):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Base_Datos_Completa', index=False)
        summary = df.groupby(['Equipo']).agg(Strings=('String_ID','count'), Promedio=('Amperios','mean')).reset_index()
        summary.to_excel(writer, sheet_name='Resumen_Por_Caja', index=False)
    return output.getvalue()

# --- EXCEL PRO ---
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

# --- CACHÉ ---
if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos_fusibles()
if 'df_med_cache' not in st.session_state: st.session_state.df_med_cache = cargar_datos_mediciones()

PLANTAS_DEF = ["El Roble", "Las Rojas"]
def cargar_plantas():
    if os.path.exists("plantas_config.json"):
        try: return json.load(open("plantas_config.json"))
        except: return PLANTAS_DEF
    return PLANTAS_DEF
plantas = cargar_plantas()

# ================= INTERFAZ =================
st.title("⚡ Gestor PMGD: Ingeniería & Análisis")
if st.button("🔄 Sincronizar Datos"): 
    st.session_state.df_cache = cargar_datos_fusibles()
    st.session_state.df_med_cache = cargar_datos_mediciones()
    st.rerun()

with st.sidebar:
    st.header("Parámetros")
    planta_sel = st.selectbox("Planta:", plantas)
    with st.expander("🛠️ Admin"):
        if st.button("Agregar Planta") and (nueva := st.text_input("Nombre")): plantas.append(nueva); json.dump(plantas, open("plantas_config.json",'w')); st.rerun()

tab1, tab2, tab3 = st.tabs(["📝 Registro Fallas", "⚡ Mediciones de Campo", "📊 Centro de Informes"])

# --- TAB 1 ---
with tab1:
    st.subheader(f"Bitácora Fallas: {planta_sel}")
    with st.form("entry"):
        c1, c2, c3, c4 = st.columns(4)
        fecha = c1.date_input("Fecha", pd.Timestamp.now())
        inv = c2.number_input("Inv #", 1, 50, 1)
        cja = c3.number_input("Caja #", 1, 100, 1)
        str_n = c4.number_input("String #", 1, 30, 1)
        c5, c6, c7 = st.columns(3)
        pol = c5.selectbox("Polaridad", ["Positivo (+)", "Negativo (-)"])
        amp = c6.number_input("Amperios (A)", 0.0, 30.0, 0.0, step=0.1)
        nota = c7.text_input("Obs. Técnica")
        if st.form_submit_button("💾 Guardar Falla", type="primary"):
            guardar_falla({'Fecha': pd.to_datetime(fecha), 'Planta': planta_sel, 'Inversor': f"Inv-{inv}", 'Caja': f"CB-{cja}", 'String': f"Str-{str_n}", 'Polaridad': pol, 'Amperios': amp, 'Nota': nota})
            st.session_state.df_cache = cargar_datos_fusibles(); st.success("Guardado."); st.rerun()
    st.divider()
    df_show = st.session_state.df_cache.copy()
    if not df_show.empty:
        df_p = df_show[df_show['Planta'] == planta_sel]
        if not df_p.empty:
            for i, row in df_p.tail(5).sort_index(ascending=False).iterrows():
                cols = st.columns([1, 2, 2, 1, 1, 1])
                cols[0].write(f"{row['Fecha'].strftime('%d/%m')}")
                cols[1].write(f"**{row['Inversor']} > {row['Caja']}**")
                cols[2].write(f"{crear_id_tecnico(row)}")
                cols[3].write(f"⚡ {row['Amperios']}A")
                if row['Nota']: cols[4].caption(row['Nota'])
                if cols[5].button("🗑️", key=f"del_{i}"): borrar_registro(i); st.rerun()

# --- TAB 2 ---
with tab2:
    st.subheader("⚡ Levantamiento de Curvas I-V")
    c_conf1, c_conf2, c_conf3 = st.columns(3)
    with c_conf1: m_inv = st.number_input("Inversor Auditado", 1, 50, 1, key="m_inv"); m_caja = st.number_input("Caja Auditada", 1, 100, 1, key="m_caja")
    with c_conf2: n_strings = st.number_input("Cant. Strings", 4, 32, 12, step=2); m_fecha = st.date_input("Fecha Medición", pd.Timestamp.now(), key="m_fecha")
    with c_conf3: st.write("---"); st.write(f"**Equipo:** Inv-{m_inv} > CB-{m_caja}")

    st.divider()
    if 'data_medicion' not in st.session_state or len(st.session_state['data_medicion']) != n_strings:
        st.session_state['data_medicion'] = pd.DataFrame({'String ID': [f"Str-{i+1}" for i in range(n_strings)], 'Amperios': [0.0] * n_strings})

    col_editor, col_stats = st.columns([1, 1])
    with col_editor:
        st.markdown("### 📝 Ingreso")
        df_editado = st.data_editor(st.session_state['data_medicion'], column_config={"Amperios": st.column_config.NumberColumn("Corriente (A)", min_value=0, max_value=20, step=0.1, format="%.1f A")}, hide_index=True, use_container_width=True, height=(35 * n_strings) + 40)
    with col_stats:
        st.markdown("### 📊 Diagnóstico Experto")
        vals = df_editado['Amperios']; vals_clean = vals[vals > 0]
        if not vals_clean.empty:
            prom = vals_clean.mean(); dev = vals_clean.std(); cv = (dev/prom)*100 if prom > 0 else 0
            k1, k2 = st.columns(2); k1.metric("Promedio", f"{prom:.2f} A"); k2.metric("Dispersión", f"{cv:.1f}%", delta_color="inverse" if cv > 5 else "normal")
            estado_txt, detalle_ia, color_box, strings_malos = generar_diagnostico_mediciones(df_editado)
            if color_box == "error": st.error(f"🚨 **{estado_txt}**"); st.markdown(detalle_ia)
            else: st.success(f"✅ **{estado_txt}**"); st.caption(detalle_ia)
            
            df_editado['Estado'] = df_editado['String ID'].apply(lambda x: 'CRÍTICO' if x in strings_malos else 'OK')
            color_map = {'OK': '#2e86c1', 'CRÍTICO': '#e74c3c'}
            fig_box = px.bar(df_editado, x='String ID', y='Amperios', title="Perfil de Corrientes", color='Estado', color_discrete_map=color_map)
            fig_box.add_hline(y=prom, line_dash="dash", line_color="orange")
            st.plotly_chart(fig_box, use_container_width=True)
            
            st.divider(); st.markdown("#### 📤 Reporte Individual (Caja)")
            comentarios_gerencia = st.text_area("Comentarios a Gerencia:", placeholder="Observaciones...", height=100)
            evidencias = st.file_uploader("📸 Evidencia", accept_multiple_files=True, type=['png', 'jpg', 'jpeg'])
            
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1:
                if st.button("💾 Guardar en Base de Datos", type="primary"): 
                    guardar_medicion_masiva(df_editado, planta_sel, f"Inv-{m_inv} > CB-{m_caja}", m_fecha)
            with c_btn2:
                kpis_pdf = {'promedio': f"{prom:.1f} A", 'dispersion': f"{cv:.1f}%", 'estado': estado_txt}
                pdf_bytes_med = crear_pdf_mediciones(planta_sel, f"Inv-{m_inv} > CB-{m_caja}", m_fecha.strftime("%d-%m-%Y"), df_editado, kpis_pdf, comentarios_gerencia, fig_box, evidencias)
                st.download_button("📄 Descargar PDF de la Caja", pdf_bytes_med, f"Medicion_CB_{m_caja}.pdf", "application/pdf")
        else: st.info("Ingresa datos.")

# --- TAB 3 ---
with tab3:
    st.header("📊 Centro de Informes y Estadísticas")
    modo_informe = st.radio("Seleccionar Tipo de Informe:", ["📉 Reporte de Fallas (Fusibles)", "⚡ Reporte de Mediciones (Protocolos)"], horizontal=True)
    st.divider()

    if "Fallas" in modo_informe:
        df = st.session_state.df_cache
        if not df.empty:
            col_filtro, col_kpi = st.columns([1, 3])
            with col_filtro:
                st.markdown("⏱️ **Filtros**")
                filtro_t = st.radio("Periodo:", ["Todo", "Este Mes", "Último Trimestre", "Último Año", "Mes Específico"])
                df_f = df[df['Planta'] == planta_sel].copy()
                df_f['ID_Tecnico'] = df_f.apply(crear_id_tecnico, axis=1)
                df_f['Equipo_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
                hoy = pd.Timestamp.now()
                if filtro_t == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
                elif filtro_t == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
                elif filtro_t == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]
                elif filtro_t == "Mes Específico":
                    c_m, c_a = st.columns(2)
                    mm = c_m.selectbox("Mes", range(1,13), index=hoy.month-1)
                    aa = c_a.number_input("Año", 2023, 2030, hoy.year)
                    df_f = df_f[(df_f['Fecha'].dt.month == mm) & (df_f['Fecha'].dt.year == aa)]

            with col_kpi:
                kpi_data = {'total': len(df_f), 'promedio': f"{df_f['Amperios'].mean():.1f} A", 'critico': df_f['Equipo_Full'].mode()[0] if not df_f['Equipo_Full'].mode().empty else "-", 'repes': df_f['Equipo_Full'].value_counts().max() if not df_f.empty else 0}
                k1, k2, k3, k4 = st.columns([1, 1, 1.5, 1])
                k1.metric("Fallas", kpi_data['total']); k2.metric("Promedio", kpi_data['promedio']); k3.metric("Equipo Crítico", kpi_data['critico']); k4.metric("Repeticiones", kpi_data['repes'])

            st.subheader("Análisis Visual")
            c1, c2, c3 = st.columns(3)
            # FIX: Quitamos showlegend=True del layout general para evitar conflicto
            l_cfg = dict(margin=dict(l=10, r=10, t=30, b=10), height=350)
            fig_rank=None; fig_pie=None
            with c1:
                if not df_f.empty: 
                    df_rk = df_f.groupby('Equipo_Full').agg(Fallas=('Fecha','count')).reset_index().sort_values('Fallas',ascending=True)
                    fig_rank = px.bar(df_rk, x='Fallas', y='Equipo_Full', orientation='h', color='Fallas', title="Ranking Fallas"); fig_rank.update_layout(**l_cfg, showlegend=False); st.plotly_chart(fig_rank, use_container_width=True)
            with c2:
                if not df_f.empty: 
                    fig_pie = px.pie(df_f, names='Inversor', hole=0.4, title="Inversores", color_discrete_sequence=px.colors.qualitative.Prism); fig_pie.update_layout(**l_cfg, showlegend=False); st.plotly_chart(fig_pie, use_container_width=True)
            with c3:
                if not df_f.empty: fig_pol = px.pie(df_f, names='Polaridad', hole=0.4, title="Polaridad", color_discrete_sequence=['#EF553B','#636EFA']); fig_pol.update_layout(**l_cfg); st.plotly_chart(fig_pol, use_container_width=True)

            c_ia, c_man = st.columns(2); txt_ia = generar_analisis_auto(df_f)
            with c_ia: st.info("🤖 Análisis IA"); st.markdown(txt_ia)
            with c_man: txt_final = st.text_area("Edita tus conclusiones:", value=st.session_state.get('texto_informe', ''), height=150)

            col_pdf, col_xls = st.columns(2)
            with col_pdf:
                if not df_f.empty and fig_rank and fig_pie: 
                    pdf_bytes = crear_pdf_gerencial(planta_sel, filtro_t, kpi_data, txt_ia, txt_final, fig_rank, fig_pie)
                    st.download_button("📄 Descargar PDF Gerencial", pdf_bytes, f"Reporte_Gerencial_{planta_sel}.pdf", "application/pdf", type="primary")
        else: st.info("Sin datos de fallas.")

    else:
        df_med = st.session_state.df_med_cache
        if not df_med.empty:
            df_m_plant = df_med[df_med['Planta'] == planta_sel]
            st.info("📊 Protocolo de Pruebas: Resumen masivo de mediciones.")
            k1, k2 = st.columns(2)
            k1.metric("Total Strings Medidos", len(df_m_plant)); k2.metric("Cajas Auditadas", df_m_plant['Equipo'].nunique() if 'Equipo' in df_m_plant.columns else 0)
            st.dataframe(df_m_plant, use_container_width=True)
            xls_data = generar_excel_maestro_mediciones(df_m_plant, planta_sel)
            st.download_button("📥 Descargar Protocolo Excel Completo", xls_data, f"Protocolo_Mediciones_{planta_sel}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
        else: st.warning("No hay mediciones.")
