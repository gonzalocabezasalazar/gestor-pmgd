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
st.set_page_config(page_title="Gestor PMGD", layout="wide", initial_sidebar_state="expanded")

# --- CONEXIÓN ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "DB_FUSIBLES"

def conectar_google_sheets():
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
        st.error("🚫 Error de Llaves."); st.stop()
            
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
        if 'Amperios' in df.columns: df['Amperios'] = pd.to_numeric(df['Amperios'], errors='coerce').fillna(0)
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
        st.toast("Eliminado", icon="🗑️")
    except: st.error("Error al borrar")

# --- UTILIDADES ---
def crear_id_tecnico(row):
    try:
        i = str(row['Inversor']).replace('Inv-', '')
        c = str(row['Caja']).replace('CB-', '')
        s = str(row['String']).replace('Str-', '')
        p = "(+)" if "Positivo" in str(row['Polaridad']) else "(-)"
        return f"{i}-{c}-{s} {p}"
    except: return "Error"

def generar_excel(df, planta):
    output = io.BytesIO()
    if df.empty: return None
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Datos')
    except: return None
    return output.getvalue()

if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos()

PLANTAS_DEF = ["El Roble", "Las Rojas"]
def cargar_plantas():
    if os.path.exists("plantas_config.json"):
        try: return json.load(open("plantas_config.json"))
        except: return PLANTAS_DEF
    return PLANTAS_DEF
plantas = cargar_plantas()

# ================= INTERFAZ =================

st.title("⚡ Monitor PMGD")

if st.button("🔄 Actualizar"):
    st.session_state.df_cache = cargar_datos()
    st.rerun()

with st.sidebar:
    st.header("Planta")
    planta_sel = st.selectbox("Seleccionar:", plantas)
    st.divider()
    with st.expander("Admin Plantas"):
        nueva = st.text_input("Nueva Planta")
        if st.button("Agregar") and nueva:
            plantas.append(nueva)
            with open("plantas_config.json", 'w') as f: json.dump(plantas, f)
            st.rerun()

tab1, tab2 = st.tabs(["📝 Ingreso", "📊 Estadísticas"])

# --- TAB 1: INGRESO ---
with tab1:
    st.subheader(f"Registro: {planta_sel}")
    with st.form("form_ingreso"):
        c1, c2, c3, c4 = st.columns(4)
        fecha = c1.date_input("Fecha", pd.Timestamp.now())
        inv = c2.number_input("Inversor", 1, 50, 1)
        cja = c3.number_input("Caja", 1, 100, 1)
        str_n = c4.number_input("String", 1, 30, 1)
        c5, c6, c7 = st.columns(3)
        pol = c5.selectbox("Polaridad", ["Positivo (+)", "Negativo (-)"])
        amp = c6.number_input("Amperios", 0.0, 30.0, 0.0, step=0.1)
        nota = c7.text_input("Nota")
        
        if st.form_submit_button("💾 Guardar", type="primary"):
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
                st.success("Guardado."); st.rerun()

    st.divider()
    df_show = st.session_state.df_cache.copy()
    if not df_show.empty:
        df_p = df_show[df_show['Planta'] == planta_sel]
        if not df_p.empty:
            for i, row in df_p.tail(5).sort_index(ascending=False).iterrows():
                id_tec = crear_id_tecnico(row)
                cols = st.columns([1, 2, 2, 1, 1, 1])
                cols[0].write(f"{row['Fecha'].strftime('%d/%m')}")
                cols[1].write(f"**{row['Inversor']} > {row['Caja']}**")
                cols[2].write(f"{id_tec}")
                cols[3].write(f"⚡ {row['Amperios']}A")
                if row['Nota']: cols[4].caption(row['Nota'])
                if cols[5].button("🗑️", key=f"del_{i}"): borrar_registro_google(i); st.rerun()

# --- TAB 2: ESTADISTICAS ---
with tab2:
    df = st.session_state.df_cache
    if not df.empty:
        # FILTROS
        st.write("⏱️ **Filtros de Tiempo**")
        filtro = st.radio("Ver:", ["Todo", "Este Mes", "Último Trimestre", "Último Semestre", "Último Año"], horizontal=True)
        
        df_f = df[df['Planta'] == planta_sel].copy()
        df_f['Equipo'] = df_f['Inversor'] + " > " + df_f['Caja']
        df_f['ID_Tecnico'] = df_f.apply(crear_id_tecnico, axis=1) # Para hover y tabla

        hoy = pd.Timestamp.now()
        if filtro == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
        elif filtro == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
        elif filtro == "Último Semestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=180))]
        elif filtro == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]

        # KPIs
        st.divider()
        k1, k2, k3 = st.columns(3)
        k1.metric("Total Fallas", len(df_f))
        k2.metric("Promedio Amperios", f"{df_f['Amperios'].mean():.1f} A")
        top = df_f['Equipo'].mode()
        k3.metric("Equipo Crítico", top[0] if not top.empty else "-")

        # GRÁFICOS SOLICITADOS
        st.divider()
        col_g1, col_g2 = st.columns([2, 1]) # 2/3 para Barras, 1/3 para Torta

        with col_g1:
            st.subheader("Ranking de Criticidad (Heatmap)")
            if not df_f.empty:
                # Agrupar datos + Lista de strings para el Hover
                df_rank = df_f.groupby('Equipo').agg(
                    Fallas=('Fecha', 'count'),
                    Detalle=('ID_Tecnico', lambda x: list(x))
                ).reset_index().sort_values('Fallas', ascending=True)

                # GRÁFICO DE BARRAS "TÉRMICO"
                # color='Fallas' crea la barra vertical de calor
                # color_continuous_scale='Reds' hace que vaya de blanco/rosa a Rojo Puro
                fig_bar = px.bar(df_rank, x='Fallas', y='Equipo', orientation='h', 
                                 text='Fallas',
                                 color='Fallas', 
                                 color_continuous_scale='Reds', # Escala de rojos
                                 hover_data=['Detalle'])
                
                st.plotly_chart(fig_bar, use_container_width=True)
            else: st.info("Sin datos.")

        with col_g2:
            st.subheader("Polaridad")
            if not df_f.empty:
                # GRÁFICO DE TORTA (Recuperado)
                fig_pie = px.pie(df_f, names='Polaridad', 
                                 color_discrete_sequence=['#EF553B', '#636EFA'], # Rojo/Azul aprox
                                 hole=0.4)
                st.plotly_chart(fig_pie, use_container_width=True)
            else: st.info("Sin datos.")

        # TABLA DETALLADA
        st.divider()
        st.subheader("Detalle Operativo")
        st.dataframe(df_f[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']], use_container_width=True)
        
        excel_data = generar_excel(df_f, planta_sel)
        if excel_data:
            st.download_button("📥 Descargar Excel", excel_data, f"Reporte_{planta_sel}.xlsx")

    else: st.info("Base de datos vacía.")
