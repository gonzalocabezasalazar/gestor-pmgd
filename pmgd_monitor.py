import streamlit as st
import pandas as pd
import plotly.express as px
import io
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import timedelta

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor PMGD Ingeniería", layout="wide", initial_sidebar_state="expanded")

# --- CONEXIÓN INTELIGENTE ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "DB_FUSIBLES"

def conectar_google_sheets():
    creds = None
    local_file = "credentials.json"
    if os.path.exists(local_file):
        try: creds = ServiceAccountCredentials.from_json_keyfile_name(local_file, SCOPE)
        except: pass
    if creds is None:
        try:
            if "gcp_service_account" in st.secrets:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), SCOPE)
        except: pass
    
    if creds is None:
        st.error("🚫 Error de Llaves: No se encuentran credentials.json ni Secrets.")
        st.stop()
            
    try: return gspread.authorize(creds).open(SHEET_NAME).sheet1
    except Exception as e: st.error(f"Error Conexión: {e}"); st.stop()

# --- GESTIÓN DE DATOS ---
def cargar_datos():
    sheet = conectar_google_sheets()
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=['Fecha', 'Planta', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota'])
        df = pd.DataFrame(data)
        if 'Fecha' in df.columns: df['Fecha'] = pd.to_datetime(df['Fecha'])
        # Asegurar columnas numéricas
        cols_num = ['Amperios']
        for c in cols_num:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        return df
    except: return pd.DataFrame()

def guardar_registro_nuevo(registro):
    sheet = conectar_google_sheets()
    reg_list = [
        registro['Fecha'].strftime("%Y-%m-%d"),
        registro['Planta'],
        registro['Inversor'],
        registro['Caja'],
        registro['String'],
        registro['Polaridad'],
        str(registro['Amperios']), # Guardamos Amperios
        registro['Nota']
    ]
    sheet.append_row(reg_list)
    st.cache_data.clear()

def borrar_registro_google(idx):
    try:
        sheet = conectar_google_sheets()
        # Corrección V12.1: delete_rows
        sheet.delete_rows(idx + 2)
        st.cache_data.clear()
        st.session_state.df_cache = cargar_datos()
        st.toast("Borrado exitoso", icon="🗑️")
    except: st.error("Error al borrar")

# --- GENERADOR DE ANÁLISIS AUTOMÁTICO ---
def generar_analisis_auto(df):
    if df.empty: return "Sin datos suficientes para análisis."
    
    total = len(df)
    equipo_top = (df['Inversor'] + " > " + df['Caja']).mode()
    equipo_critico = equipo_top[0] if not equipo_top.empty else "N/A"
    
    # Análisis de Polaridad
    pos = len(df[df['Polaridad'].str.contains("Positivo", na=False)])
    neg = len(df[df['Polaridad'].str.contains("Negativo", na=False)])
    trend_pol = "Equilibrada"
    if pos > neg * 1.5: trend_pol = "Predominancia POSITIVA (Posible falla a tierra DC)"
    if neg > pos * 1.5: trend_pol = "Predominancia NEGATIVA"

    texto = (f"ANÁLISIS AUTOMÁTICO:\n"
             f"Se han registrado {total} eventos en el periodo seleccionado.\n"
             f"El punto más crítico es {equipo_critico}, el cual presenta la mayor recurrencia.\n"
             f"Tendencia de Polaridad: {trend_pol}.\n"
             f"Promedio de corriente registrada: {df['Amperios'].mean():.1f} A.")
    return texto

# --- EXCEL PRO CON ANÁLISIS ---
def generar_excel_profesional(df_reporte, planta, periodo, analisis_manual):
    output = io.BytesIO()
    if df_reporte.empty: return None
    df_rep = df_reporte.copy()
    df_rep['Equipo'] = df_rep['Inversor'] + " > " + df_rep['Caja']
    
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb = writer.book
            ws = wb.add_worksheet('Reporte Ingeniería')
            ws.hide_gridlines(2)
            
            # Estilos
            f_titulo = wb.add_format({'bold': True, 'font_size': 16, 'color': '#ffffff', 'bg_color': '#2e86c1', 'align': 'center'})
            f_sub = wb.add_format({'bold': True, 'font_size': 12, 'bottom': 1})
            f_wrap = wb.add_format({'text_wrap': True, 'border': 1, 'valign': 'top'})
            
            # Encabezado
            ws.merge_range('B2:H2', f"INFORME TÉCNICO DE FALLAS: {planta.upper()}", f_titulo)
            ws.write('B3', f"Periodo: {periodo}")
            ws.write('E3', f"Fecha Emisión: {pd.Timestamp.now().strftime('%d-%m-%Y')}")
            
            # Sección Análisis
            ws.write('B5', "ANÁLISIS TÉCNICO (Ingeniería):", f_sub)
            ws.merge_range('B6:H10', analisis_manual, f_wrap)
            
            # Datos principales
            ws.write('B12', "DETALLE DE OPERACIONES:", f_sub)
            df_export = df_rep[['Fecha', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']]
            df_export.to_excel(writer, sheet_name='Reporte Ingeniería', startrow=12, startcol=1, index=False)
            
            # Gráfico simple en Excel
            chart = wb.add_chart({'type': 'column'})
            chart.add_series({
                'name': 'Amperios',
                'categories': ['Reporte Ingeniería', 13, 1, 13+len(df_export)-1, 1], # Fechas
                'values':     ['Reporte Ingeniería', 13, 6, 13+len(df_export)-1, 6], # Amperios
            })
            ws.insert_chart('J6', chart)
            
    except: return None
    return output.getvalue()

# --- CACHÉ ---
if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos()

# Config Plantas
PLANTAS_DEF = ["El Roble", "Las Rojas"]
def cargar_plantas():
    if os.path.exists("plantas_config.json"):
        try: return json.load(open("plantas_config.json"))
        except: return PLANTAS_DEF
    return PLANTAS_DEF
plantas = cargar_plantas()

# --- INTERFAZ ---
st.title("⚡ Gestor PMGD: Ingeniería & Análisis")

if st.button("🔄 Sincronizar Datos"):
    st.session_state.df_cache = cargar_datos()
    st.rerun()

with st.sidebar:
    st.header("Parámetros")
    planta_sel = st.selectbox("Planta:", plantas)
    st.divider()
    with st.expander("🛠️ Admin"):
        nueva = st.text_input("Nueva Planta")
        if st.button("Agregar") and nueva:
            plantas.append(nueva)
            with open("plantas_config.json", 'w') as f: json.dump(plantas, f)
            st.rerun()

tab1, tab2 = st.tabs(["📝 Registro Técnico", "📈 Análisis & Reportes"])

# --- PESTAÑA 1: REGISTRO ---
with tab1:
    st.subheader(f"Bitácora: {planta_sel}")
    with st.form("entry"):
        c1, c2, c3, c4 = st.columns(4)
        fecha = c1.date_input("Fecha", pd.Timestamp.now())
        inv = c2.number_input("Inv #", 1, 50, 1)
        cja = c3.number_input("Caja #", 1, 100, 1)
        str_n = c4.number_input("String #", 1, 30, 1)
        
        c5, c6, c7 = st.columns(3)
        pol = c5.selectbox("Polaridad", ["Positivo (+)", "Negativo (-)"])
        # AQUI AGREGAMOS EL CAMPO AMPERIOS
        amp = c6.number_input("Amperios (A)", 0.0, 30.0, 0.0, step=0.1, help="Corriente medida o nominal del fusible")
        nota = c7.text_input("Obs. Técnica")
        
        if st.form_submit_button("💾 Registrar Evento", type="primary"):
            df = st.session_state.df_cache
            # Validación
            dup = df[(df['Planta']==planta_sel) & (df['Fecha']==pd.to_datetime(fecha)) & 
                     (df['Inversor']==f"Inv-{inv}") & (df['Caja']==f"CB-{cja}") & 
                     (df['String']==f"Str-{str_n}")] if not df.empty else pd.DataFrame()
            if not dup.empty: st.error("Duplicado detectado.")
            else:
                new_data = {'Fecha': pd.to_datetime(fecha), 'Planta': planta_sel, 
                            'Inversor': f"Inv-{inv}", 'Caja': f"CB-{cja}", 'String': f"Str-{str_n}", 
                            'Polaridad': pol, 'Amperios': amp, 'Nota': nota}
                guardar_registro_nuevo(new_data)
                st.session_state.df_cache = cargar_datos()
                st.success("Registrado.")
                st.rerun()

    st.markdown("### 📋 Últimos Eventos")
    df_show = st.session_state.df_cache.copy()
    if not df_show.empty:
        df_p = df_show[df_show['Planta'] == planta_sel]
        if not df_p.empty:
            for i, row in df_p.tail(5).sort_index(ascending=False).iterrows():
                # Formato Tarjeta
                cols = st.columns([1, 2, 2, 1, 1, 1])
                cols[0].write(f"📅 {row['Fecha'].strftime('%d/%m')}")
                cols[1].write(f"**{row['Inversor']} > {row['Caja']}**")
                cols[2].write(f"🔌 {row['String']} ({row['Polaridad']})")
                cols[3].write(f"⚡ **{row['Amperios']} A**")
                
                # --- CORRECCION DEL ERROR VISUAL ---
                # Antes: cols[4].info(...) if ... (Esto provocaba el texto raro)
                # Ahora: Estructura if limpia
                if row['Nota']:
                    cols[4].info(row['Nota'])
                
                if cols[5].button("🗑️", key=f"del_{i}"): borrar_registro_google(i); st.rerun()
        else: st.info("Sin registros.")

# --- PESTAÑA 2: REPORTES ---
with tab2:
    st.header("Laboratorio de Datos")
    df = st.session_state.df_cache
    if not df.empty:
        # FILTROS DE TIEMPO
        col_filtro, col_kpi = st.columns([1, 3])
        with col_filtro:
            st.markdown("⏱️ **Periodo de Análisis**")
            filtro_t = st.radio("Ver:", ["Todo", "Este Mes", "Último Trimestre", "Último Semestre"])
            
            # Lógica de Filtro
            df_f = df[df['Planta'] == planta_sel].copy()
            hoy = pd.Timestamp.now()
            if filtro_t == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
            elif filtro_t == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
            elif filtro_t == "Último Semestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=180))]
        
        # KPIs
        with col_kpi:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Fallas Totales", len(df_f))
            k2.metric("Amperaje Promedio", f"{df_f['Amperios'].mean():.1f} A")
            
            top_eq = (df_f['Inversor'] + " " + df_f['Caja']).mode()
            k3.metric("Equipo Crítico", top_eq[0] if not top_eq.empty else "-")
            
            # Cálculo de recurrencia máxima
            if not df_f.empty:
                df_f['ID_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
                max_rec = df_f['ID_Full'].value_counts().max()
                k4.metric("Máx. Repeticiones", max_rec)

        st.divider()

        # GRAFICO 1: CRONOLOGÍA DE FALLAS (SCATTER)
        st.subheader("1. Cronología de Fallas (Repetición en el Tiempo)")
        st.caption("Detecta patrones visuales: ¿Se repite el mismo fusible en corto tiempo?")
        if not df_f.empty:
            fig_timeline = px.scatter(df_f, x="Fecha", y="ID_Full", color="Polaridad", size="Amperios",
                                      title="Distribución Temporal de Fallas",
                                      hover_data=['String', 'Nota'],
                                      height=400)
            st.plotly_chart(fig_timeline, use_container_width=True)

        # GRAFICO 2: PARETO
        c_g1, c_g2 = st.columns(2)
        with c_g1:
            st.subheader("2. Ranking de Equipos")
            conteo = df_f['ID_Full'].value_counts().reset_index()
            conteo.columns = ['Equipo', 'Fallas']
            st.plotly_chart(px.bar(conteo, x='Fallas', y='Equipo', orientation='h', color='Fallas'), use_container_width=True)
        
        with c_g2:
            st.subheader("3. Distribución Polaridad")
            st.plotly_chart(px.pie(df_f, names='Polaridad', hole=0.4), use_container_width=True)

        st.divider()
        
        # ZONA DE ANÁLISIS TÉCNICO
        st.subheader("📝 Generación de Informe Técnico")
        
        col_txt1, col_txt2 = st.columns(2)
        
        # Análisis Automático
        with col_txt1:
            st.info("🤖 Análisis IA (Preliminar)")
            analisis_ia = generar_analisis_auto(df_f)
            st.write(analisis_ia)
            if st.button("Copiar al Informe Manual"):
                st.session_state['texto_informe'] = analisis_ia
        
        # Análisis Manual
        with col_txt2:
            st.warning("👷 Análisis del Ingeniero (Para el Excel)")
            texto_final = st.text_area("Edita tus conclusiones aquí:", 
                                       value=st.session_state.get('texto_informe', ''), 
                                       height=150,
                                       key='texto_informe_input')

        # BOTÓN DESCARGA
        excel_data = generar_excel_profesional(df_f, planta_sel, filtro_t, texto_final)
        if excel_data:
            st.download_button("📥 Descargar Informe Oficial (Excel)", excel_data, 
                               f"Informe_{planta_sel}_{filtro_t}.xlsx", 
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                               type="primary")
            
    else: st.info("Selecciona una planta con datos para ver el laboratorio.")
