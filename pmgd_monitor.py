import streamlit as st
import pandas as pd
import plotly.express as px
import io
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import timedelta
import numpy as np

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor PMGD Pro", layout="wide", initial_sidebar_state="expanded")

# --- CONEXIÓN MULTI-HOJA ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "DB_FUSIBLES" # Tu archivo principal

def conectar_google_sheets(hoja_nombre):
    """Conecta a una hoja específica dentro del archivo"""
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
            
    try: 
        # Abrimos el archivo y seleccionamos la hoja específica
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        # Intentamos abrir la hoja, si no existe la primera (sheet1)
        try: return spreadsheet.worksheet(hoja_nombre)
        except: return spreadsheet.sheet1
    except Exception as e: st.error(f"Error Conexión: {e}"); st.stop()

# --- GESTIÓN DE DATOS (FUSIBLES) ---
def cargar_datos_fusibles():
    sheet = conectar_google_sheets("Sheet1") # O el nombre que tenga tu hoja de fallas
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

# --- GESTIÓN DE DATOS (MEDICIONES NUEVAS) ---
def guardar_medicion_masiva(df_mediciones, planta, equipo, fecha):
    """Guarda una ráfaga de datos de una caja completa"""
    sheet = conectar_google_sheets("DB_MEDICIONES") # Asegúrate de crear esta hoja en Google Sheets
    
    # Preparamos los datos para subir en bloque (es más rápido)
    filas_para_subir = []
    fecha_str = fecha.strftime("%Y-%m-%d")
    
    for idx, row in df_mediciones.iterrows():
        # Formato: Fecha, Planta, Equipo (Inv-1 > CB-2), String_ID, Amperios
        fila = [fecha_str, planta, equipo, row['String ID'], row['Amperios']]
        filas_para_subir.append(fila)
    
    # Subida masiva
    sheet.append_rows(filas_para_subir)
    st.toast(f"✅ Guardados {len(filas_para_subir)} strings correctamente")

# --- UTILS ---
def crear_id_tecnico(row):
    try:
        i = str(row['Inversor']).replace('Inv-', '')
        c = str(row['Caja']).replace('CB-', '')
        s = str(row['String']).replace('Str-', '')
        p = "(+)" if "Positivo" in str(row['Polaridad']) else "(-)"
        return f"{i}-{c}-{s} {p}"
    except: return "Error"

def generar_excel_pro(df, planta, periodo, comentarios):
    output = io.BytesIO()
    if df.empty: return None
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb = writer.book
            ws = wb.add_worksheet('Reporte Técnico')
            ws.hide_gridlines(2)
            f_header = wb.add_format({'bold': True, 'bg_color': '#2e86c1', 'color': 'white'})
            
            ws.write('B2', f"REPORTE: {planta} ({periodo})", f_header)
            ws.write('B4', "Comentarios:", wb.add_format({'bold':True}))
            ws.write('C4', comentarios)
            
            df_chart = df['Inversor'].value_counts().reset_index()
            df_chart.columns = ['Inversor', 'Fallas']
            df_chart.to_excel(writer, sheet_name='Reporte Técnico', startrow=10, startcol=10, index=False)
            
            chart = wb.add_chart({'type': 'pie'})
            chart.add_series({
                'name': 'Fallas',
                'categories': ['Reporte Técnico', 11, 10, 11+len(df_chart)-1, 10],
                'values': ['Reporte Técnico', 11, 11, 11+len(df_chart)-1, 11],
                'data_labels': {'percentage': True}
            })
            ws.insert_chart('G6', chart)
            
            df_export = df[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']].copy()
            df_export['Fecha'] = df_export['Fecha'].dt.date
            df_export.to_excel(writer, sheet_name='Reporte Técnico', startrow=10, startcol=1, index=False)
    except: return None
    return output.getvalue()

if 'df_cache' not in st.session_state: st.session_state.df_cache = cargar_datos_fusibles()

PLANTAS_DEF = ["El Roble", "Las Rojas"]
def cargar_plantas():
    if os.path.exists("plantas_config.json"):
        try: return json.load(open("plantas_config.json"))
        except: return PLANTAS_DEF
    return PLANTAS_DEF
plantas = cargar_plantas()

# ================= INTERFAZ =================

st.title("⚡ Gestor PMGD: O&M Profesional")

if st.button("🔄 Sincronizar"):
    st.session_state.df_cache = cargar_datos_fusibles()
    st.rerun()

with st.sidebar:
    st.header("Planta")
    planta_sel = st.selectbox("Seleccionar:", plantas)
    st.divider()

# PESTAÑAS PRINCIPALES
tab1, tab2, tab3 = st.tabs(["📝 Registro Fallas", "⚡ Mediciones de Campo", "📊 Estadísticas"])

# --- TAB 1: REGISTRO FALLAS (MANTENIDO) ---
with tab1:
    st.subheader(f"Bitácora Fallas: {planta_sel}")
    with st.form("entry"):
        c1, c2, c3, c4 = st.columns(4)
        fecha = c1.date_input("Fecha", pd.Timestamp.now())
        inv = c2.number_input("Inversor", 1, 50, 1)
        cja = c3.number_input("Caja", 1, 100, 1)
        str_n = c4.number_input("String", 1, 30, 1)
        c5, c6, c7 = st.columns(3)
        pol = c5.selectbox("Polaridad", ["Positivo (+)", "Negativo (-)"])
        amp = c6.number_input("Amperios (A)", 0.0, 30.0, 0.0, step=0.1)
        nota = c7.text_input("Nota")
        
        if st.form_submit_button("💾 Guardar Falla", type="primary"):
            new_data = {'Fecha': pd.to_datetime(fecha), 'Planta': planta_sel, 
                        'Inversor': f"Inv-{inv}", 'Caja': f"CB-{cja}", 'String': f"Str-{str_n}", 
                        'Polaridad': pol, 'Amperios': amp, 'Nota': nota}
            guardar_falla(new_data)
            st.session_state.df_cache = cargar_datos_fusibles()
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
                if cols[5].button("🗑️", key=f"del_{i}"): borrar_registro(i); st.rerun()

# --- TAB 2: MEDICIONES DE CAMPO (NUEVO) ---
with tab2:
    st.subheader("⚡ Levantamiento de Curvas I-V (Simulado)")
    st.info("Ingresa las corrientes medidas en terreno para detectar desequilibrios.")
    
    col_conf1, col_conf2, col_conf3 = st.columns(3)
    
    with col_conf1:
        m_inv = st.number_input("Inversor Auditado", 1, 50, 1, key="m_inv")
        m_caja = st.number_input("Caja Auditada", 1, 100, 1, key="m_caja")
    
    with col_conf2:
        # AQUÍ ESTÁ LA MAGIA: Selector dinámico de strings
        n_strings = st.number_input("¿Cuántos Strings tiene esta caja?", 
                                    min_value=4, max_value=32, value=12, step=2, 
                                    help="Ajusta esto según la realidad de la caja en terreno")
        m_fecha = st.date_input("Fecha Medición", pd.Timestamp.now(), key="m_fecha")
        
    with col_conf3:
        st.write("---")
        st.caption("Configuración:")
        st.write(f"**Equipo:** Inv-{m_inv} > CB-{m_caja}")
        st.write(f"**Capacidad:** {n_strings} Entradas")

    st.divider()

    # GENERADOR DE GRID (EXCEL STYLE)
    # Inicializamos un DataFrame vacío con la cantidad de filas que dijo el usuario
    if 'data_medicion' not in st.session_state or len(st.session_state['data_medicion']) != n_strings:
        st.session_state['data_medicion'] = pd.DataFrame({
            'String ID': [f"Str-{i+1}" for i in range(n_strings)],
            'Amperios': [0.0] * n_strings
        })

    col_editor, col_stats = st.columns([1, 1])

    with col_editor:
        st.markdown("### 📝 Ingreso Rápido")
        # El editor permite escribir rápido como en Excel
        df_editado = st.data_editor(
            st.session_state['data_medicion'],
            column_config={
                "Amperios": st.column_config.NumberColumn(
                    "Corriente (A)",
                    help="Valor medido con pinza",
                    min_value=0,
                    max_value=20,
                    step=0.1,
                    format="%.1f A"
                )
            },
            hide_index=True,
            use_container_width=True,
            height=(35 * n_strings) + 40 # Altura dinámica según filas
        )

    # ESTADÍSTICAS EN TIEMPO REAL
    with col_stats:
        st.markdown("### 📊 Diagnóstico en Vivo")
        
        vals = df_editado['Amperios']
        vals_clean = vals[vals > 0] # Ignoramos los ceros para el promedio
        
        if not vals_clean.empty:
            promedio = vals_clean.mean()
            total_box = vals.sum()
            desviacion = vals_clean.std()
            coef_var = (desviacion / promedio) * 100 if promedio > 0 else 0
            
            # KPIs
            k1, k2 = st.columns(2)
            k1.metric("Corriente Total Caja", f"{total_box:.1f} A")
            k2.metric("Promedio String", f"{promedio:.2f} A")
            
            k3, k4 = st.columns(2)
            k3.metric("Dispersión (CV)", f"{coef_var:.1f}%", 
                      delta_color="inverse" if coef_var > 5 else "normal") # Rojo si dispersión > 5%
            
            # Identificar String Débil
            min_val = vals_clean.min()
            idx_min = vals_clean.idxmin()
            str_min = df_editado.loc[idx_min, 'String ID']
            
            st.write("---")
            if coef_var > 5:
                st.error(f"⚠️ **Desequilibrio Detectado:** La dispersión es alta ({coef_var:.1f}%).")
                st.write(f"El string más bajo es **{str_min}** con **{min_val} A**.")
                st.progress(min(1.0, coef_var/20)) # Barra de alerta
            else:
                st.success("✅ La caja está balanceada (Dispersión < 5%)")

            # Gráfico de Barras de la Caja
            fig_box = px.bar(df_editado, x='String ID', y='Amperios', 
                             title=f"Perfil de Corrientes: Inv-{m_inv} > CB-{m_caja}")
            # Línea de promedio
            fig_box.add_hline(y=promedio, line_dash="dash", line_color="red", annotation_text="Promedio")
            st.plotly_chart(fig_box, use_container_width=True)
            
            # Botón Guardar
            if st.button("💾 Guardar Medición en Base de Datos", type="primary"):
                equipo_full = f"Inv-{m_inv} > CB-{m_caja}"
                guardar_medicion_masiva(df_editado, planta_sel, equipo_full, m_fecha)

        else:
            st.info("Ingresa valores en la tabla para ver el diagnóstico.")

# --- TAB 3: ESTADÍSTICAS (MANTENIDO) ---
with tab3:
    df = st.session_state.df_cache
    if not df.empty:
        col_filtro, col_kpi = st.columns([1, 3])
        with col_filtro:
            st.markdown("⏱️ **Filtros**")
            filtro_t = st.radio("Ver:", ["Todo", "Este Mes", "Último Año"], horizontal=False)
            df_f = df[df['Planta'] == planta_sel].copy()
            df_f['ID_Tecnico'] = df_f.apply(crear_id_tecnico, axis=1)
            df_f['Equipo_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
            hoy = pd.Timestamp.now()
            if filtro_t == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
            elif filtro_t == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]

        with col_kpi:
            k1, k2, k3, k4 = st.columns([1, 1, 1.5, 1])
            k1.metric("Fallas", len(df_f))
            k2.metric("Promedio A", f"{df_f['Amperios'].mean():.1f} A")
            top_eq = df_f['Equipo_Full'].mode()
            k3.metric("Equipo Crítico", top_eq[0] if not top_eq.empty else "-")
            k4.metric("Repeticiones", df_f['Equipo_Full'].value_counts().max() if not df_f.empty else 0)

        st.divider()
        c1, c2, c3 = st.columns(3)
        layout_cfg = dict(margin=dict(l=10, r=10, t=30, b=10), showlegend=True, height=350)

        with c1:
            st.caption("Ranking Equipos")
            if not df_f.empty:
                df_rank = df_f.groupby('Equipo_Full').agg(
                    Fallas=('Fecha', 'count'),
                    Strings=('ID_Tecnico', lambda x: list(x))
                ).reset_index().sort_values('Fallas', ascending=True)
                fig = px.bar(df_rank, x='Fallas', y='Equipo_Full', orientation='h', color='Fallas', hover_data=['Strings'])
                fig.update_layout(**layout_cfg)
                st.plotly_chart(fig, use_container_width=True)

        with c2:
            st.caption("Inversores")
            if not df_f.empty:
                fig = px.pie(df_f, names='Inversor', hole=0.4, color_discrete_sequence=px.colors.qualitative.Prism)
                fig.update_traces(textposition='inside', textinfo='percent+label')
                fig.update_layout(**layout_cfg)
                fig.update_layout(showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

        with c3:
            st.caption("Polaridad")
            if not df_f.empty:
                fig = px.pie(df_f, names='Polaridad', hole=0.4, color_discrete_sequence=['#EF553B', '#636EFA'])
                fig.update_layout(**layout_cfg)
                st.plotly_chart(fig, use_container_width=True)
                
        # DESCARGA
        st.divider()
        excel = generar_excel_pro(df_f, planta_sel, filtro_t, "Reporte generado desde App")
        if excel:
            st.download_button("📥 Descargar Reporte (Excel)", excel, f"Reporte_{planta_sel}.xlsx", 
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
            
    else: st.info("Sin datos.")
