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
st.set_page_config(page_title="Monitor Planta Solar", layout="wide", initial_sidebar_state="expanded")

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
        # Blindaje contra hojas vacías para evitar KeyError
        if not data: return pd.DataFrame(columns=['Fecha', 'Planta', 'Equipo', 'String ID', 'Amperios'])
        df = pd.DataFrame(data)
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

# --- GENERADOR DE PDF ---
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
    if not isinstance(text, str): return str(text)
    replacements = {'•': '-', '—': '-', '–': '-', '“': '"', '”': '"', '‘': "'", '’': "'", 'ñ': 'n', 'Ñ': 'N', 'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U'}
    for k, v in replacements.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'ignore').decode('latin-1')

# 1. PDF GERENCIAL (FALLAS)
def crear_pdf_gerencial(planta, periodo, kpis, ia_text, engineer_text, fig_rank, fig_pie, fig_pol):
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
    
    # SECCIÓN GRÁFICOS
    pdf.add_page(); pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, "ANEXO GRAFICO", 0, 1, 'C'); pdf.ln(5)
    try:
        if fig_rank:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1: 
                fig_rank.write_image(t1.name, width=800, height=400); pdf.image(t1.name, x=10, w=190); pdf.ln(10)
        if fig_pie:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t2: 
                fig_pie.write_image(t2.name, width=500, height=350); pdf.image(t2.name, x=10, w=90)
        if fig_pol:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t3: 
                fig_pol.write_image(t3.name, width=500, height=350); pdf.set_y(pdf.get_y()-0); pdf.image(t3.name, x=110, y=pdf.get_y()-65 if fig_pie else pdf.get_y(), w=90)
    except: pass
    
    return bytes(pdf.output(dest='S'))

# 2. PDF MEDICIONES
def crear_pdf_mediciones(planta, equipo, fecha, df_data, kpis, comentarios_gerencia, fig_box, evidencias):
    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"REPORTE DE MEDICION DE CAMPO"), 0, 1, 'C'); pdf.ln(5)
    pdf.set_font("Arial", "", 10); pdf.cell(0, 8, clean_text(f"Planta: {planta}"), 0, 1); pdf.cell(0, 8, clean_text(f"Equipo: {equipo}"), 0, 1); pdf.cell(0, 8, clean_text(f"Fecha: {fecha}"), 0, 1); pdf.ln(5)
    
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
    
    pdf.set_font("Arial", "B", 10); pdf.cell(40, 8, "String", 1, 0, 'C', True); pdf.cell(40, 8, "Valor", 1, 0, 'C', True); pdf.cell(60, 8, "Estado", 1, 1, 'C', True); pdf.set_font("Arial", "", 10)
    for index, row in df_data.iterrows():
        pdf.cell(40, 8, clean_text(str(row['String ID'])), 1, 0, 'C'); pdf.cell(40, 8, f"{row['Amperios']:.1f} A", 1, 0, 'C')
        st_txt = "CRITICO" if row['Estado'] == 'CRÍTICO' else "OK"
        if st_txt == "CRITICO": pdf.set_text_color(200, 0, 0)
        else: pdf.set_text_color(0, 100, 0)
        pdf.cell(60, 8, st_txt, 1, 1, 'C'); pdf.set_text_color(0, 0, 0)
        
    if evidencias:
        pdf.add_page(); pdf.set_font("Arial", "B", 14); pdf.cell(0, 10, clean_text("EVIDENCIA FOTOGRAFICA"), 0, 1, 'C'); pdf.ln(10)
        for img in evidencias:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tf: tf.write(img.getbuffer()); pdf.image(tf.name, w=170); pdf.ln(5)
            except: pass
    return bytes(pdf.output(dest='S'))

# 3. EXCEL
def generar_excel_maestro_mediciones(df, planta):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Base_Datos', index=False)
    return output.getvalue()

def generar_excel_pro(df_reporte, planta, periodo, comentarios):
    output = io.BytesIO()
    if df_reporte.empty: return None
    df_rep = df_reporte.copy()
    df_rep['ID_Tecnico'] = df_rep.apply(crear_id_tecnico, axis=1)
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_rep.to_excel(writer, sheet_name='Reporte', index=False)
    return output.getvalue()

# --- APP START ---
if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos_fusibles()
if 'df_med_cache' not in st.session_state: st.session_state.df_med_cache = cargar_datos_mediciones()

PLANTAS_DEF = ["El Roble", "Las Rojas"]
def cargar_plantas():
    try: return json.load(open("plantas_config.json"))
    except: return PLANTAS_DEF
plantas = cargar_plantas()

# --- INTERFAZ ---
st.title("⚡ Monitor Planta Solar")
if st.button("🔄 Sincronizar"): st.session_state.df_cache=cargar_datos_fusibles(); st.session_state.df_med_cache=cargar_datos_mediciones(); st.rerun()

with st.sidebar:
    st.header("Configuración")
    planta_sel = st.selectbox("Planta:", plantas)
    with st.expander("Admin"):
        if st.button("Agregar") and (n := st.text_input("Nombre")): plantas.append(n); json.dump(plantas, open("plantas_config.json",'w')); st.rerun()

t1, t2, t3 = st.tabs(["📝 Fallas", "⚡ Mediciones", "📊 Informes"])

with t1:
    st.subheader(f"Registro: {planta_sel}")
    with st.form("f1"):
        c1,c2,c3,c4=st.columns(4); f=c1.date_input("Fecha"); i=c2.number_input("Inv",1,50); c=c3.number_input("Caja",1,100); s=c4.number_input("Str",1,30)
        c5,c6,c7=st.columns(3); p=c5.selectbox("Pol",["Positivo (+)","Negativo (-)"]); a=c6.number_input("A",0.0,30.0); n=c7.text_input("Nota")
        if st.form_submit_button("Guardar"): guardar_falla({'Fecha':pd.to_datetime(f),'Planta':planta_sel,'Inversor':f"Inv-{i}",'Caja':f"CB-{c}",'String':f"Str-{s}",'Polaridad':p,'Amperios':a,'Nota':n}); st.session_state.df_cache=cargar_datos_fusibles(); st.rerun()
    
    df_s = st.session_state.df_cache[st.session_state.df_cache['Planta']==planta_sel]
    if not df_s.empty:
        for idx, r in df_s.tail(5).sort_index(ascending=False).iterrows():
            cols = st.columns([1,2,2,1,1,1])
            cols[0].write(r['Fecha'].strftime('%d/%m')); cols[1].write(f"{r['Inversor']}>{r['Caja']}"); cols[2].write(crear_id_tecnico(r)); cols[3].write(f"{r['Amperios']}A"); cols[4].caption(r['Nota'])
            if cols[5].button("🗑️", key=f"d{idx}"): borrar_registro(idx); st.rerun()

with t2:
    st.subheader("Mediciones")
    c1,c2,c3 = st.columns(3); mi=c1.number_input("Inv",1,50,key="mi"); mc=c2.number_input("Caja",1,100,key="mc"); ns=c3.number_input("Cant",4,32,12); mf=c3.date_input("Fecha",key="mf")
    
    if 'data_med' not in st.session_state or len(st.session_state['data_med']) != ns:
        st.session_state['data_med'] = pd.DataFrame({'String ID': [f"Str-{i+1}" for i in range(ns)], 'Amperios': [0.0]*ns})
    
    ce, cs = st.columns([1,1])
    df_ed = ce.data_editor(st.session_state['data_med'], height=(35*ns)+40, hide_index=True)
    vals = df_ed['Amperios']; v_cl = vals[vals>0]
    
    if not v_cl.empty:
        prom = v_cl.mean(); dev = v_cl.std(); cv = (dev/prom)*100 if prom>0 else 0
        cs.metric("Promedio", f"{prom:.2f} A"); cs.metric("Dispersión", f"{cv:.1f}%", delta_color="inverse" if cv>5 else "normal")
        stt, det, col, bad = generar_diagnostico_mediciones(df_ed)
        if col=="error": cs.error(stt) 
        else: cs.success(stt)
        
        df_ed['Estado'] = df_ed['String ID'].apply(lambda x: 'CRÍTICO' if x in bad else 'OK')
        fig = px.bar(df_ed, x='String ID', y='Amperios', color='Estado', color_discrete_map={'OK':'#2e86c1','CRÍTICO':'#e74c3c'}); fig.add_hline(y=prom, line_dash="dash", line_color="orange"); cs.plotly_chart(fig, use_container_width=True)
        
        st.divider(); comm = st.text_area("Notas:"); imgs = st.file_uploader("Fotos", accept_multiple_files=True)
        cb1, cb2 = st.columns(2)
        if cb1.button("💾 Guardar"): guardar_medicion_masiva(df_ed, planta_sel, f"Inv-{mi}>CB-{mc}", mf)
        kpis={'promedio':f"{prom:.1f}",'dispersion':f"{cv:.1f}%",'estado':stt}
        cb2.download_button("📄 PDF Caja", crear_pdf_mediciones(planta_sel, f"Inv-{mi}>CB-{mc}", mf.strftime("%d-%m-%Y"), df_ed, kpis, comm, fig, imgs), f"Med_{mc}.pdf")

with t3:
    st.header("Informes")
    mode = st.radio("Tipo:", ["Fallas", "Mediciones"], horizontal=True); st.divider()
    
    if mode == "Fallas":
        df = st.session_state.df_cache; df_f = df[df['Planta']==planta_sel].copy()
        if not df_f.empty:
            df_f['Equipo_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
            
            # --- FILTROS RECUPERADOS (AQUÍ ESTÁ LA SOLUCIÓN) ---
            c_f, c_k = st.columns([1,3])
            with c_f:
                st.markdown("⏱️ **Filtros Temporales**")
                filtro_t = st.radio("Periodo:", ["Todo", "Este Mes", "Último Trimestre", "Último Semestre", "Último Año", "Mes Específico"])
                
                hoy = pd.Timestamp.now()
                if filtro_t == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
                elif filtro_t == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
                elif filtro_t == "Último Semestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=180))]
                elif filtro_t == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]
                elif filtro_t == "Mes Específico":
                    mm = st.selectbox("Mes", range(1,13), index=hoy.month-1)
                    aa = st.number_input("Año", 2023, 2030, hoy.year)
                    df_f = df_f[(df_f['Fecha'].dt.month == mm) & (df_f['Fecha'].dt.year == aa)]

            with c_k:
                kpis = {'total':len(df_f), 'promedio':f"{df_f['Amperios'].mean():.1f} A", 'critico': df_f['Equipo_Full'].mode()[0] if not df_f.empty else "-", 'repes':0}
                k1, k2, k3, k4 = st.columns([1, 1, 1.5, 1])
                k1.metric("Fallas", kpis['total']); k2.metric("Promedio", kpis['promedio']); k3.metric("Equipo Crítico", kpis['critico']); k4.metric("Repeticiones", kpis['repes'])

            st.subheader("Análisis Visual")
            c1,c2,c3=st.columns(3); l_cfg=dict(margin=dict(l=10,r=10,t=30,b=10), height=300)
            
            # --- AQUÍ ESTÁ EL FIX DE COLORES (template="plotly_white") ---
            drk=df_f.groupby('Equipo_Full').agg(Fallas=('Fecha','count')).reset_index().sort_values('Fallas',ascending=True)
            frk=px.bar(drk, x='Fallas', y='Equipo_Full', orientation='h', title="Ranking", template="plotly_white"); frk.update_layout(**l_cfg, showlegend=False); c1.plotly_chart(frk, use_container_width=True)
            
            fpi=px.pie(df_f, names='Inversor', title="Inversores", color_discrete_sequence=px.colors.qualitative.Prism, template="plotly_white"); fpi.update_layout(**l_cfg, showlegend=False); c2.plotly_chart(fpi, use_container_width=True)
            fpo=px.pie(df_f, names='Polaridad', title="Polaridad", color_discrete_sequence=['#EF553B','#636EFA'], template="plotly_white"); fpo.update_layout(**l_cfg); c3.plotly_chart(fpo, use_container_width=True)
            
            ia=generar_analisis_auto(df_f); st.info(ia); txt=st.text_area("Conclusiones:")
            if st.download_button("📄 PDF Reporte", crear_pdf_gerencial(planta_sel, filtro_t, kpis, ia, txt, frk, fpi, fpo), "Reporte.pdf"): pass
        else: st.info("Sin datos.")
    else:
        dfm = st.session_state.df_med_cache; dfmp = dfm[dfm['Planta']==planta_sel]
        if not dfmp.empty:
            st.dataframe(dfmp)
            st.download_button("📥 Excel", generar_excel_maestro_mediciones(dfmp, planta_sel), "Protocolo.xlsx")
        else: st.warning("Sin mediciones.")
