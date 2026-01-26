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
st.set_page_config(page_title="Gestor PMGD Pro", layout="wide", initial_sidebar_state="expanded")

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

# --- INTELIGENCIA (IA) ---
def crear_id_tecnico(row):
    try:
        i = str(row['Inversor']).replace('Inv-', '')
        c = str(row['Caja']).replace('CB-', '')
        s = str(row['String']).replace('Str-', '')
        p = "(+)" if "Positivo" in str(row['Polaridad']) else "(-)"
        return f"{i}-{c}-{s} {p}"
    except: return "Error"

def generar_analisis_auto(df):
    if df.empty: return "Sin datos para análisis."
    
    total = len(df)
    eq_mode = (df['Inversor'] + " > " + df['Caja']).mode()
    critico = eq_mode[0] if not eq_mode.empty else "N/A"
    
    pos = len(df[df['Polaridad'].astype(str).str.contains("Positivo")])
    neg = len(df[df['Polaridad'].astype(str).str.contains("Negativo")])
    
    trend = "Equilibrada"
    if pos > neg * 1.3: trend = "PREDOMINANCIA POSITIVA (+)"
    if neg > pos * 1.3: trend = "PREDOMINANCIA NEGATIVA (-)"
    
    return (f"RESUMEN AUTOMÁTICO:\n"
            f"- Se registraron {total} eventos en el periodo.\n"
            f"- El equipo más afectado es {critico}.\n"
            f"- Tendencia de Polaridad: {trend} ({pos} vs {neg}).\n"
            f"- Promedio de corriente de falla: {df['Amperios'].mean():.1f} A.")

# --- EXCEL PRO ---
def generar_excel_pro(df, planta, periodo, comentarios):
    output = io.BytesIO()
    if df.empty: return None
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb = writer.book
            ws = wb.add_worksheet('Reporte Técnico')
            ws.hide_gridlines(2)
            
            f_titulo = wb.add_format({'bold': True, 'font_size': 16, 'color': 'white', 'bg_color': '#C0392B', 'align': 'center'})
            f_sub = wb.add_format({'bold': True, 'bottom': 1})
            f_texto = wb.add_format({'text_wrap': True, 'border': 1, 'valign': 'top'})
            f_fecha = wb.add_format({'num_format': 'yyyy-mm-dd', 'align': 'left'})
            
            ws.merge_range('B2:H2', f"REPORTE DE FALLAS: {planta.upper()}", f_titulo)
            ws.write('B3', f"Periodo: {periodo}")
            ws.write('E3', f"Fecha: {pd.Timestamp.now().strftime('%d-%m-%Y')}")
            
            ws.write('B5', "ANÁLISIS TÉCNICO:", f_sub)
            ws.merge_range('B6:F10', comentarios, f_texto)
            
            df_chart = df['Inversor'].value_counts().reset_index()
            df_chart.columns = ['Inversor', 'Cantidad']
            df_chart.to_excel(writer, sheet_name='Reporte Técnico', startrow=5, startcol=10, index=False)
            
            chart = wb.add_chart({'type': 'pie'})
            chart.add_series({
                'name': 'Distribución de Fallas',
                'categories': ['Reporte Técnico', 6, 10, 6 + len(df_chart) - 1, 10],
                'values':     ['Reporte Técnico', 6, 11, 6 + len(df_chart) - 1, 11],
                'data_labels': {'percentage': True},
            })
            chart.set_title({'name': 'Fusibles Operados por Inversor'})
            chart.set_style(10)
            ws.insert_chart('G6', chart, {'x_scale': 0.9, 'y_scale': 0.9})

            ws.write('B13', "DETALLE DE EVENTOS:", f_sub)
            df_export = df[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']].copy()
            df_export['Fecha'] = df_export['Fecha'].dt.date
            df_export.to_excel(writer, sheet_name='Reporte Técnico', startrow=13, startcol=1, index=False)
            
            ws.set_column('B:B', 12, f_fecha)
            ws.set_column('C:C', 15)
            ws.set_column('H:H', 30)
            
    except Exception as e: return None
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

st.title("⚡ Monitor PMGD: Ingeniería")

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

tab1, tab2 = st.tabs(["📝 Ingreso", "📊 Estadísticas & Informe"])

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

with tab2:
    df = st.session_state.df_cache
    if not df.empty:
        st.write("⏱️ **Filtros de Tiempo**")
        filtro = st.radio("Ver:", ["Todo", "Este Mes", "Último Trimestre", "Último Semestre", "Último Año"], horizontal=True)
        
        df_f = df[df['Planta'] == planta_sel].copy()
        df_f['Equipo'] = df_f['Inversor'] + " > " + df_f['Caja']
        df_f['ID_Tecnico'] = df_f.apply(crear_id_tecnico, axis=1)

        hoy = pd.Timestamp.now()
        if filtro == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
        elif filtro == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
        elif filtro == "Último Semestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=180))]
        elif filtro == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]

        st.divider()
        k1, k2, k3 = st.columns(3)
        k1.metric("Total Fallas", len(df_f))
        k2.metric("Promedio Amperios", f"{df_f['Amperios'].mean():.1f} A")
        top = df_f['Equipo'].mode()
        k3.metric("Equipo Crítico", top[0] if not top.empty else "-")

        st.divider()
        c1, c2, c3 = st.columns(3) 
        layout_cfg = dict(margin=dict(l=10, r=10, t=30, b=10), showlegend=True, height=350)
        
        with c1:
            st.subheader("Ranking")
            if not df_f.empty:
                # --- CORRECCIÓN AQUÍ: Agregamos 'Detalle' al agrupamiento ---
                df_rank = df_f.groupby('Equipo').agg(
                    Fallas=('Fecha', 'count'),
                    Detalle=('ID_Tecnico', lambda x: list(x)) # Esto recupera los IDs
                ).reset_index().sort_values('Fallas', ascending=True)
                
                fig = px.bar(df_rank, x='Fallas', y='Equipo', orientation='h', text='Fallas', 
                             color='Fallas', color_continuous_scale='Reds',
                             hover_data=['Detalle']) # Mostramos el detalle en el hover
                
                fig.update_layout(**layout_cfg)
                fig.update_coloraxes(showscale=False) 
                st.plotly_chart(fig, use_container_width=True)
            else: st.info("Sin datos.")

        with c2:
            st.subheader("Inversores")
            if not df_f.empty:
                fig = px.pie(df_f, names='Inversor', color_discrete_sequence=px.colors.qualitative.Prism, hole=0.4)
                fig.update_traces(textposition='inside', textinfo='percent+label')
                fig.update_layout(**layout_cfg)
                fig.update_layout(showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            else: st.info("Sin datos.")

        with c3:
            st.subheader("Polaridad")
            if not df_f.empty:
                fig = px.pie(df_f, names='Polaridad', color_discrete_sequence=['#EF553B', '#636EFA'], hole=0.4)
                fig.update_layout(**layout_cfg)
                fig.update_layout(legend=dict(orientation="h", y=-0.1))
                st.plotly_chart(fig, use_container_width=True)
            else: st.info("Sin datos.")

        st.divider()
        st.subheader("🧠 Centro de Análisis")
        col_ia, col_man = st.columns(2)
        texto_ia = generar_analisis_auto(df_f)
        
        with col_ia:
            st.info("🤖 Análisis Automático (IA)")
            st.write(texto_ia)
            if st.button("Copiar IA al Informe 👉"):
                st.session_state['borrador_informe'] = texto_ia

        with col_man:
            st.warning("📝 Informe Técnico del Ingeniero")
            comentarios_finales = st.text_area("Edita tus conclusiones aquí:", 
                                               value=st.session_state.get('borrador_informe', ''),
                                               height=150)

        st.divider()
        st.subheader("Detalle & Exportación")
        st.dataframe(df_f[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']], use_container_width=True)
        
        excel_data = generar_excel_pro(df_f, planta_sel, filtro, comentarios_finales)
        if excel_data:
            st.download_button("📥 Descargar Reporte Profesional (Excel)", excel_data, 
                               f"Informe_{planta_sel}.xlsx", 
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               type="primary")

    else: st.info("Base de datos vacía.")
