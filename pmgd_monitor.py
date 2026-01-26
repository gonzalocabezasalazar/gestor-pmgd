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
        str(registro['Amperios']),
        registro['Nota']
    ]
    sheet.append_row(reg_list)
    st.cache_data.clear()

def borrar_registro_google(idx):
    try:
        sheet = conectar_google_sheets()
        sheet.delete_rows(idx + 2)
        st.cache_data.clear()
        st.session_state.df_cache = cargar_datos()
        st.toast("Borrado exitoso", icon="🗑️")
    except: st.error("Error al borrar")

# --- GENERADOR DE CÓDIGO TÉCNICO ---
def crear_id_tecnico(row):
    """Convierte los datos en formato 1-12-24 (+)"""
    try:
        # Extraer números de los textos (ej: "Inv-1" -> "1")
        i = str(row['Inversor']).replace('Inv-', '')
        c = str(row['Caja']).replace('CB-', '')
        s = str(row['String']).replace('Str-', '')
        # Polaridad corta
        p = "(+)" if "Positivo" in str(row['Polaridad']) else "(-)"
        return f"{i}-{c}-{s} {p}"
    except:
        return "Error-ID"

# --- GENERADOR DE ANÁLISIS AUTOMÁTICO ---
def generar_analisis_auto(df):
    if df.empty: return "Sin datos suficientes."
    total = len(df)
    equipo_top = (df['Inversor'] + " > " + df['Caja']).mode()
    critico = equipo_top[0] if not equipo_top.empty else "N/A"
    pos = len(df[df['Polaridad'].str.contains("Positivo", na=False)])
    neg = len(df[df['Polaridad'].str.contains("Negativo", na=False)])
    trend = "Positiva" if pos > neg else "Negativa"
    return (f"ANÁLISIS AUTOMÁTICO:\nTotal Eventos: {total}.\nEquipo Crítico: {critico}.\n"
            f"Tendencia Polaridad: {trend} ({pos} vs {neg}).\nPromedio Corriente: {df['Amperios'].mean():.1f} A.")

# --- EXCEL PRO ---
def generar_excel_profesional(df_reporte, planta, periodo, analisis_manual):
    output = io.BytesIO()
    if df_reporte.empty: return None
    df_rep = df_reporte.copy()
    
    # Agregar columna ID Técnico al Excel también
    df_rep['ID_Tecnico'] = df_rep.apply(crear_id_tecnico, axis=1)
    
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb = writer.book
            ws = wb.add_worksheet('Reporte Ingeniería')
            ws.hide_gridlines(2)
            f_titulo = wb.add_format({'bold': True, 'font_size': 16, 'color': '#ffffff', 'bg_color': '#2e86c1', 'align': 'center'})
            f_sub = wb.add_format({'bold': True, 'font_size': 12, 'bottom': 1})
            f_wrap = wb.add_format({'text_wrap': True, 'border': 1, 'valign': 'top'})
            
            ws.merge_range('B2:H2', f"INFORME: {planta.upper()}", f_titulo)
            ws.write('B3', f"Periodo: {periodo}")
            ws.write('B5', "ANÁLISIS:", f_sub)
            ws.merge_range('B6:H9', analisis_manual, f_wrap)
            ws.write('B11', "DETALLE:", f_sub)
            
            # Columnas a exportar
            cols_export = ['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']
            df_rep[cols_export].to_excel(writer, sheet_name='Reporte Ingeniería', startrow=11, startcol=1, index=False)
            
    except: return None
    return output.getvalue()

# --- CACHÉ ---
if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos()

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
        amp = c6.number_input("Amperios (A)", 0.0, 30.0, 0.0, step=0.1)
        nota = c7.text_input("Obs. Técnica")
        
        if st.form_submit_button("💾 Registrar", type="primary"):
            df = st.session_state.df_cache
            dup = df[(df['Planta']==planta_sel) & (df['Fecha']==pd.to_datetime(fecha)) & 
                     (df['Inversor']==f"Inv-{inv}") & (df['Caja']==f"CB-{cja}") & 
                     (df['String']==f"Str-{str_n}")] if not df.empty else pd.DataFrame()
            if not dup.empty: st.error("Duplicado.")
            else:
                new_data = {'Fecha': pd.to_datetime(fecha), 'Planta': planta_sel, 
                            'Inversor': f"Inv-{inv}", 'Caja': f"CB-{cja}", 'String': f"Str-{str_n}", 
                            'Polaridad': pol, 'Amperios': amp, 'Nota': nota}
                guardar_registro_nuevo(new_data)
                st.session_state.df_cache = cargar_datos()
                st.success("OK"); st.rerun()

    st.markdown("### 📋 Últimos Eventos")
    df_show = st.session_state.df_cache.copy()
    if not df_show.empty:
        df_p = df_show[df_show['Planta'] == planta_sel]
        if not df_p.empty:
            for i, row in df_p.tail(5).sort_index(ascending=False).iterrows():
                # Cálculo de ID en vuelo para mostrar
                id_tec = crear_id_tecnico(row)
                cols = st.columns([1, 2, 2, 1, 1, 1])
                cols[0].write(f"📅 {row['Fecha'].strftime('%d/%m')}")
                cols[1].write(f"**{row['Inversor']} > {row['Caja']}**")
                cols[2].write(f"🔌 {id_tec}") # Aquí mostramos el ID técnico
                cols[3].write(f"⚡ {row['Amperios']}A")
                if row['Nota']: cols[4].info(row['Nota'])
                if cols[5].button("🗑️", key=f"del_{i}"): borrar_registro_google(i); st.rerun()
        else: st.info("Sin registros.")

with tab2:
    st.header("Laboratorio de Datos")
    df = st.session_state.df_cache
    if not df.empty:
        col_filtro, col_kpi = st.columns([1, 3])
        with col_filtro:
            st.markdown("⏱️ **Filtros**")
            filtro_t = st.radio("Periodo:", ["Todo", "Este Mes", "Último Trimestre"])
            df_f = df[df['Planta'] == planta_sel].copy()
            
            # Crear Columna ID Técnico para todo el DataFrame filtrado
            df_f['ID_Tecnico'] = df_f.apply(crear_id_tecnico, axis=1)
            df_f['Equipo_Full'] = df_f['Inversor'] + " > " + df_f['Caja']

            hoy = pd.Timestamp.now()
            if filtro_t == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
            elif filtro_t == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
        
        with col_kpi:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Fallas", len(df_f))
            k2.metric("Promedio A", f"{df_f['Amperios'].mean():.1f}")
            top = df_f['Equipo_Full'].mode()
            k3.metric("Equipo Crítico", top[0] if not top.empty else "-")
            k4.metric("Máx. Repetición", df_f['Equipo_Full'].value_counts().max() if not df_f.empty else 0)

        st.divider()

        # --- GRAFICO 1: RANKING CON TOOLTIP MEJORADO ---
        c_g1, c_g2 = st.columns([2, 1])
        with c_g1:
            st.subheader("📊 Ranking de Fallas (Hover para detalle)")
            if not df_f.empty:
                # Agrupamos para contar fallas por Equipo, pero guardamos la lista de Strings únicos
                df_rank = df_f.groupby('Equipo_Full').agg(
                    Fallas=('Fecha', 'count'),
                    Detalle_Strings=('ID_Tecnico', lambda x: list(x)) # Crea lista de strings
                ).reset_index().sort_values('Fallas', ascending=True)
                
                fig = px.bar(df_rank, x='Fallas', y='Equipo_Full', orientation='h', 
                             hover_data=['Detalle_Strings'], # Esto muestra la lista al pasar el mouse
                             text='Fallas')
                st.plotly_chart(fig, use_container_width=True)

        with c_g2:
            st.subheader("Distribución")
            st.plotly_chart(px.pie(df_f, names='Polaridad', hole=0.4), use_container_width=True)

        # --- TABLA DETALLADA (SOLICITUD RESTAURACIÓN) ---
        st.divider()
        st.subheader("📋 Detalle de Operaciones (Base de Datos)")
        
        # Seleccionamos y renombramos columnas para que se vea profesional
        tabla_final = df_f[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']].sort_values('Fecha', ascending=False)
        st.dataframe(tabla_final, use_container_width=True, hide_index=True)

        # ZONA INFORMES
        st.divider()
        st.subheader("📝 Exportación")
        col_txt1, col_txt2 = st.columns(2)
        with col_txt1:
            st.info("🤖 IA Analysis")
            ia_text = generar_analisis_auto(df_f)
            st.write(ia_text)
            if st.button("Usar IA"): st.session_state['texto_informe'] = ia_text
        with col_txt2:
            texto_final = st.text_area("Conclusiones:", value=st.session_state.get('texto_informe', ''), height=100)

        excel = generar_excel_profesional(df_f, planta_sel, filtro_t, texto_final)
        if excel:
            st.download_button("📥 Descargar Reporte Completo", excel, f"Reporte_{planta_sel}.xlsx", 
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")

    else: st.info("Sin datos para mostrar análisis.")
