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

# --- CONEXIÓN SEGURA (NUBE + LOCAL) ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "DB_FUSIBLES"

def conectar_google_sheets():
    creds = None
    # 1. Intento Local
    if os.path.exists("credentials.json"):
        try: creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
        except: pass
    
    # 2. Intento Nube (Secrets)
    if creds is None:
        try:
            if "gcp_service_account" in st.secrets:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), SCOPE)
        except: pass
    
    if creds is None:
        st.error("🚫 Error Crítico: No se encuentran las llaves de acceso.")
        st.stop()
            
    try: return gspread.authorize(creds).open(SHEET_NAME).sheet1
    except Exception as e: st.error(f"Error de Conexión: {e}"); st.stop()

# --- GESTIÓN DE DATOS ---
def cargar_datos():
    sheet = conectar_google_sheets()
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=['Fecha', 'Planta', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota'])
        df = pd.DataFrame(data)
        if 'Fecha' in df.columns: df['Fecha'] = pd.to_datetime(df['Fecha'])
        # Asegurar columnas numéricas
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
        sheet.delete_rows(idx + 2) # Fix gspread v6
        st.cache_data.clear()
        st.session_state.df_cache = cargar_datos()
        st.toast("Registro eliminado", icon="🗑️")
    except: st.error("No se pudo borrar el registro.")

# --- HELPERS ---
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

# --- CACHÉ ---
if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos()

# --- PLANTAS ---
PLANTAS_DEF = ["El Roble", "Las Rojas"]
def cargar_plantas():
    if os.path.exists("plantas_config.json"):
        try: return json.load(open("plantas_config.json"))
        except: return PLANTAS_DEF
    return PLANTAS_DEF
plantas = cargar_plantas()

# ================= INTERFAZ GRÁFICA =================

st.title("⚡ Monitor PMGD")

# Botón recarga manual
if st.button("🔄 Actualizar Datos"):
    st.session_state.df_cache = cargar_datos()
    st.rerun()

# Sidebar
with st.sidebar:
    st.header("Planta")
    planta_sel = st.selectbox("Seleccionar:", plantas)
    st.divider()
    with st.expander("Administrar"):
        nueva = st.text_input("Nueva Planta")
        if st.button("Agregar"):
            if nueva and nueva not in plantas:
                plantas.append(nueva)
                with open("plantas_config.json", 'w') as f: json.dump(plantas, f)
                st.rerun()

# Pestañas
tab1, tab2 = st.tabs(["📝 Registro", "📊 Estadísticas"])

# --- PESTAÑA 1: INGRESO ---
with tab1:
    st.subheader(f"Ingreso de Falla: {planta_sel}")
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
            # Validación duplicados
            dup = df[(df['Planta']==planta_sel) & (df['Fecha']==pd.to_datetime(fecha)) & 
                     (df['Inversor']==f"Inv-{inv}") & (df['Caja']==f"CB-{cja}") & 
                     (df['String']==f"Str-{str_n}")] if not df.empty else pd.DataFrame()
            
            if not dup.empty:
                st.error("⛔ Este registro ya existe.")
            else:
                new_data = {
                    'Fecha': pd.to_datetime(fecha), 'Planta': planta_sel, 
                    'Inversor': f"Inv-{inv}", 'Caja': f"CB-{cja}", 'String': f"Str-{str_n}", 
                    'Polaridad': pol, 'Amperios': amp, 'Nota': nota
                }
                guardar_registro_nuevo(new_data)
                st.session_state.df_cache = cargar_datos()
                st.success("Guardado correctamente.")
                st.rerun()

    # Tabla de últimos registros con botón borrar
    st.divider()
    st.markdown("##### Últimos Registros")
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
                if cols[5].button("🗑️", key=f"del_{i}"):
                    borrar_registro_google(i)
                    st.rerun()
        else: st.info("No hay datos en esta planta.")

# --- PESTAÑA 2: GRÁFICOS (ESTILO V7) ---
with tab2:
    df = st.session_state.df_cache
    if not df.empty:
        # 1. FILTROS DE TIEMPO (LO QUE PEDISTE)
        st.markdown("**Filtros de Tiempo**")
        filtro = st.radio("Periodo:", ["Todo", "Este Mes", "Último Trimestre", "Último Semestre", "Último Año"], horizontal=True)
        
        # Aplicar filtro
        df_f = df[df['Planta'] == planta_sel].copy()
        df_f['Equipo'] = df_f['Inversor'] + " > " + df_f['Caja']
        
        hoy = pd.Timestamp.now()
        if filtro == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
        elif filtro == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
        elif filtro == "Último Semestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=180))]
        elif filtro == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]

        # KPIs Rápidos
        st.divider()
        k1, k2, k3 = st.columns(3)
        k1.metric("Total Fallas", len(df_f))
        k2.metric("Promedio Amperios", f"{df_f['Amperios'].mean():.1f} A")
        
        top = df_f['Equipo'].mode()
        k3.metric("Equipo Más Crítico", top[0] if not top.empty else "-")

        # 2. LOS GRÁFICOS (LAYOUT V7: LADO A LADO)
        st.divider()
        
        col_graf1, col_graf2 = st.columns(2)
        
        with col_graf1:
            st.subheader("Ranking de Fallas")
            # Gráfico de Barras Horizontal
            if not df_f.empty:
                conteo = df_f['Equipo'].value_counts().reset_index()
                conteo.columns = ['Equipo', 'Fallas']
                fig_bar = px.bar(conteo, x='Fallas', y='Equipo', orientation='h', text='Fallas')
                st.plotly_chart(fig_bar, use_container_width=True)
            else: st.info("Sin datos.")

        with col_graf2:
            st.subheader("Mapa de Calor (Intensidad)")
            # El Heatmap que pediste (como barra/cuadrícula)
            if not df_f.empty:
                df_heat = df_f.groupby(['Inversor', 'Caja']).size().reset_index(name='Fallas')
                fig_heat = px.density_heatmap(df_heat, x="Caja", y="Inversor", z="Fallas", 
                                              text_auto=True, color_continuous_scale="Viridis")
                st.plotly_chart(fig_heat, use_container_width=True)
            else: st.info("Sin datos.")

        # 3. TABLA FINAL Y DESCARGA
        st.divider()
        st.subheader("Detalle de Datos")
        df_f['ID_Tecnico'] = df_f.apply(crear_id_tecnico, axis=1) # Mostrar ID técnico en tabla
        st.dataframe(df_f[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Amperios', 'Nota']], use_container_width=True)
        
        excel_data = generar_excel(df_f, planta_sel)
        if excel_data:
            st.download_button("📥 Descargar Excel", excel_data, f"Reporte_{planta_sel}.xlsx")

    else:
        st.info("No hay datos cargados en la base de datos.")
