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

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Monitor Planta Solar", layout="wide", initial_sidebar_state="expanded")

# --- UTILS DE FECHA (SOLUCIÓN ERROR "ESTE MES") ---
def obtener_nombre_mes(mes_num):
    meses = {1:"Enero", 2:"Febrero", 3:"Marzo", 4:"Abril", 5:"Mayo", 6:"Junio", 
             7:"Julio", 8:"Agosto", 9:"Septiembre", 10:"Octubre", 11:"Noviembre", 12:"Diciembre"}
    return meses.get(mes_num, "")

# --- CONEXIÓN ---
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
    for idx, row in df_mediciones.iterrows():
        filas.append([f_str, planta, equipo, row['String ID'], row['Amperios']])
    sheet.append_rows(filas)
    st.cache_data.clear()
    st.toast("✅ Guardado")

# --- IA ---
def crear_id_tecnico(row):
    try: return f"{str(row['Inversor']).replace('Inv-','')}-{str(row['Caja']).replace('CB-','')}-{str(row['String']).replace('Str-','')} {'(+)' if 'Positivo' in str(row['Polaridad']) else '(-)'}"
    except: return "Error"

def generar_analisis_auto(df):
    if df.empty: return "Sin datos."
    total = len(df)
    eq = (df['Inversor'] + " > " + df['Caja']).mode()
    crit = eq[0] if not eq.empty else "N/A"
    pos = len(df[df['Polaridad'].astype(str).str.contains("Positivo")])
    neg = len(df[df['Polaridad'].astype(str).str.contains("Negativo")])
    trend = "Equilibrada"
    if pos > neg * 1.5: trend = "Predominancia POSITIVA"
    if neg > pos * 1.5: trend = "Predominancia NEGATIVA"
    return f"Total Fallas: {total}. Crítico: {crit}. Tendencia: {trend}. Promedio: {df['Amperios'].mean():.1f}A."

def generar_diagnostico_mediciones(df):
    vals = df['Amperios']
    prom = vals[vals > 0].mean() if not vals[vals > 0].empty else 0
    c0 = df[df['Amperios'] == 0]['String ID'].tolist()
    cb = df[(df['Amperios'] > 0) & (df['Amperios'] < prom * 0.90)]
    stt, col, det = "NORMAL", "success", "Parámetros normales."
    if c0 or not cb.empty: stt, col, det = "ANOMALIA", "error", "Se detectaron anomalías."
    return stt, det, col, c0 + cb['String ID'].tolist()

# --- PDF ---
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14); self.cell(0, 10, 'INFORME TECNICO PMGD', 0, 1, 'C'); self.ln(5)
    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8); self.cell(0, 10, f'Pagina {self.page_no()}', 0, 0, 'C')

def clean_text(text):
    if not isinstance(text, str): return str(text)
    replacements = {'•':'-', '—':'-', '–':'-', '“':'"', '”':'"', '‘':"'", '’':"'", 'ñ':'n', 'Ñ':'N', 'á':'a', 'é':'e', 'í':'i', 'ó':'o', 'ú':'u', 'Á':'A', 'É':'E', 'Í':'I', 'Ó':'O', 'Ú':'U', '⚡':''}
    for k, v in replacements.items(): text = text.replace(k, v)
    return text.encode('latin-1', 'replace').decode('latin-1')

def crear_pdf_gerencial(planta, periodo_texto, kpis, ia_text, engineer_text, fig_rank, fig_pie, fig_pol):
    pdf = PDF(); pdf.add_page(); pdf.set_auto_page_break(True, margin=15)
    
    # TITULO CON FECHA CORRECTA
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"Reporte Gerencial: {planta} | {periodo_texto}"), 0, 1, 'L')
    pdf.set_font("Arial", "", 10); pdf.cell(0, 10, clean_text(f"Fecha Emision: {pd.Timestamp.now().strftime('%d-%m-%Y')}"), 0, 1, 'L'); pdf.ln(5)
    
    pdf.set_fill_color(230, 240, 255); pdf.rect(10, pdf.get_y(), 190, 20, 'F')
    pdf.set_font("Arial", "B", 10); pdf.cell(47, 10, "Fallas Total",0,0,'C'); pdf.cell(47, 10, "Critico",0,0,'C'); pdf.cell(47, 10, "Promedio",0,0,'C'); pdf.cell(47, 10, "Repeticiones",0,1,'C')
    pdf.set_font("Arial", "", 12); pdf.cell(47, 10, str(kpis['total']),0,0,'C'); pdf.cell(47, 10, clean_text(str(kpis['critico'])),0,0,'C'); pdf.cell(47, 10, str(kpis['promedio']),0,0,'C'); pdf.cell(47, 10, str(kpis['repes']),0,1,'C'); pdf.ln(10)
    
    pdf.set_font("Arial", "B", 11); pdf.cell(0, 8, clean_text("Diagnostico Automatico"), 0, 1)
    pdf.set_font("Arial", "", 10); pdf.multi_cell(0, 6, clean_text(ia_text)); pdf.ln(5)
    
    pdf.set_font("Arial", "B", 11); pdf.set_text_color(200, 0, 0); pdf.cell(0, 8, clean_text("Comentarios Gerencia"), 0, 1)
    pdf.set_text_color(0, 0, 0); pdf.set_font("Arial", "", 10); pdf.multi_cell(0, 6, clean_text(engineer_text)); pdf.ln(10)
    
    pdf.add_page(); pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, "ANEXO GRAFICO", 0, 1, 'C'); pdf.ln(5)
    try:
        # SOLUCIÓN PUNTO 3: FONDO BLANCO Y ALTA CALIDAD
        img_params = dict(format="png", width=800, height=450, scale=2)
        
        if fig_rank:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1: 
                fig_rank.write_image(t1.name, **img_params); pdf.image(t1.name, x=10, w=190); pdf.ln(10)
        
        y_pos = pdf.get_y()
        pie_params = dict(format="png", width=500, height=400, scale=2)
        
        if fig_pie:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t2: 
                fig_pie.write_image(t2.name, **pie_params); pdf.image(t2.name, x=10, y=y_pos, w=90)
        if fig_pol:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t3: 
                fig_pol.write_image(t3.name, **pie_params); pdf.image(t3.name, x=110, y=y_pos, w=90)
    except: pass
    return bytes(pdf.output(dest='S'))

def crear_pdf_mediciones(planta, equipo, fecha, df_data, kpis, comentarios, fig_box, evidencias):
    pdf = PDF(); pdf.add_page(); pdf.set_auto_page_break(True, margin=15)
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text(f"REPORTE MEDICION CAMPO"), 0, 1, 'C'); pdf.ln(5)
    pdf.set_font("Arial", "", 10); pdf.cell(0, 8, clean_text(f"Planta: {planta} | Equipo: {equipo}"), 0, 1); pdf.cell(0, 8, clean_text(f"Fecha: {fecha}"), 0, 1); pdf.ln(5)
    pdf.set_fill_color(240, 240, 240); pdf.rect(10, pdf.get_y(), 190, 20, 'F')
    pdf.set_font("Arial", "B", 11); pdf.cell(63, 10, "Promedio",0,0,'C'); pdf.cell(63, 10, "Dispersion",0,0,'C'); pdf.cell(63, 10, "Estado",0,1,'C')
    pdf.set_font("Arial", "", 12); pdf.cell(63, 10, f"{kpis['promedio']}",0,0,'C'); pdf.cell(63, 10, f"{kpis['dispersion']}",0,0,'C'); pdf.cell(63, 10, clean_text(kpis['estado']),0,1,'C'); pdf.ln(10)
    pdf.set_font("Arial", "B", 12); pdf.cell(0, 10, clean_text("Informe a Gerencia"), 0, 1)
    pdf.set_font("Arial", "", 10); pdf.multi_cell(0, 6, clean_text(comentarios) if comentarios else "-"); pdf.ln(10)
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as t1: 
            fig_box.write_image(t1.name, format="png", width=800, height=350, scale=2); pdf.image(t1.name, x=10, w=190)
    except: pass
    pdf.ln(5)
    pdf.set_font("Arial", "B", 10); pdf.cell(40, 8, "String", 1, 0, 'C', True); pdf.cell(40, 8, "Valor", 1, 0, 'C', True); pdf.cell(60, 8, "Estado", 1, 1, 'C', True); pdf.set_font("Arial", "", 10)
    for i, r in df_data.iterrows():
        pdf.ln(8)
        pdf.cell(40, 8, clean_text(str(r['String ID'])), 1, 0, 'C'); pdf.cell(40, 8, f"{r['Amperios']:.1f} A", 1, 0, 'C')
        st_txt = "CRITICO" if r['Estado'] == 'CRÍTICO' else "OK"
        if st_txt=="CRITICO": pdf.set_text_color(200,0,0) 
        else: pdf.set_text_color(0,100,0)
        pdf.cell(60, 8, st_txt, 1, 0, 'C'); pdf.set_text_color(0,0,0)
    if evidencias:
        pdf.add_page(); pdf.set_font("Arial", "B", 14); pdf.cell(0, 10, "EVIDENCIA", 0, 1, 'C'); pdf.ln(10)
        for img in evidencias:
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tf: tf.write(img.getbuffer()); pdf.image(tf.name, w=160); pdf.ln(5)
            except: pass
    return bytes(pdf.output(dest='S'))

def generar_excel_maestro(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as w: df.to_excel(w, index=False)
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
        # SOLUCIÓN PUNTO 3: FONDO BLANCO EN WEB PARA PREVENIR ERRORES
        fig = px.bar(df_ed, x='String ID', y='Amperios', color='Estado', color_discrete_map={'OK':'#2e86c1','CRÍTICO':'#e74c3c'})
        fig.update_layout(plot_bgcolor='white', paper_bgcolor='white')
        fig.add_hline(y=prom, line_dash="dash", line_color="orange"); cs.plotly_chart(fig, use_container_width=True)
        
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
            
            c_f, c_k = st.columns([1,3])
            with c_f:
                st.markdown("⏱️ **Filtros**")
                filtro_t = st.radio("Periodo:", ["Todo", "Este Mes", "Mes Específico"])
                hoy = pd.Timestamp.now()
                # SOLUCIÓN PUNTO 2: TEXTO FECHA DINÁMICO
                fecha_texto = "Histórico Completo"
                
                if filtro_t == "Este Mes": 
                    df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
                    fecha_texto = f"{obtener_nombre_mes(hoy.month)} {hoy.year}"
                elif filtro_t == "Mes Específico":
                    mm = st.selectbox("Mes", range(1,13), index=hoy.month-1, format_func=lambda x: obtener_nombre_mes(x))
                    aa = st.number_input("Año", 2023, 2030, hoy.year)
                    df_f = df_f[(df_f['Fecha'].dt.month == mm) & (df_f['Fecha'].dt.year == aa)]
                    fecha_texto = f"{obtener_nombre_mes(mm)} {aa}"

            with c_k:
                # SOLUCIÓN PUNTO 1: CÁLCULO REAL DE REPETICIONES
                repes = 0; critico = "-"
                if not df_f.empty:
                    conteos = df_f['Equipo_Full'].value_counts()
                    critico = conteos.idxmax()
                    repes = conteos.max()

                kpis = {'total':len(df_f), 'promedio':f"{df_f['Amperios'].mean():.1f} A", 'critico': critico, 'repes': repes}
                k1, k2, k3, k4 = st.columns([1, 1, 1.5, 1])
                k1.metric("Fallas", kpis['total']); k2.metric("Promedio", kpis['promedio']); k3.metric("Equipo Crítico", kpis['critico']); k4.metric("Repeticiones", kpis['repes'])

            st.subheader("Análisis Visual")
            c1,c2,c3=st.columns(3)
            # SOLUCIÓN PUNTO 3: FONDO BLANCO EN TODOS LOS GRÁFICOS
            l_cfg=dict(margin=dict(l=10,r=10,t=30,b=10), height=300, paper_bgcolor='white', plot_bgcolor='white')
            
            drk=df_f.groupby('Equipo_Full').agg(Fallas=('Fecha','count')).reset_index().sort_values('Fallas',ascending=True)
            frk=px.bar(drk, x='Fallas', y='Equipo_Full', orientation='h', title="Ranking"); frk.update_layout(**l_cfg); c1.plotly_chart(frk, use_container_width=True)
            
            fpi=px.pie(df_f, names='Inversor', title="Inversores"); fpi.update_layout(**l_cfg); c2.plotly_chart(fpi, use_container_width=True)
            fpo=px.pie(df_f, names='Polaridad', title="Polaridad"); fpo.update_layout(**l_cfg); c3.plotly_chart(fpo, use_container_width=True)
            
            ia=generar_analisis_auto(df_f); st.info(ia); txt=st.text_area("Conclusiones:")
            if st.download_button("📄 PDF Reporte", crear_pdf_gerencial(planta_sel, fecha_texto, kpis, ia, txt, frk, fpi, fpo), "Reporte.pdf"): pass
        else: st.info("Sin datos.")
    else:
        dfm = st.session_state.df_med_cache; dfmp = dfm[dfm['Planta']==planta_sel]
        if not dfmp.empty:
            st.dataframe(dfmp)
            st.download_button("📥 Excel", generar_excel_maestro(dfmp), "Protocolo.xlsx")
        else: st.warning("Sin mediciones.")
