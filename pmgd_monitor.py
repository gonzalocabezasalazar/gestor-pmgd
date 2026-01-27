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

# --- UTILIDADES ---
def crear_id_tecnico(row):
    try:
        i = str(row['Inversor']).replace('Inv-', '')
        c = str(row['Caja']).replace('CB-', '')
        s = str(row['String']).replace('Str-', '')
        p = "(+)" if "Positivo" in str(row['Polaridad']) else "(-)"
        return f"{i}-{c}-{s} {p}"
    except: return "Error-ID"

def generar_analisis_auto(df):
    if df.empty: return "Sin datos."
    total = len(df)
    eq_mode = (df['Inversor'] + " > " + df['Caja']).mode()
    critico = eq_mode[0] if not eq_mode.empty else "N/A"
    pos = len(df[df['Polaridad'].astype(str).str.contains("Positivo")])
    neg = len(df[df['Polaridad'].astype(str).str.contains("Negativo")])
    trend = "Equilibrada"
    if pos > neg * 1.5: trend = "Predominancia POSITIVA (+)"
    if neg > pos * 1.5: trend = "Predominancia NEGATIVA (-)"
    return (f"ANÁLISIS AUTOMÁTICO:\nTotal Eventos: {total}.\nEquipo Crítico: {critico}.\n"
            f"Tendencia: {trend}.\nPromedio Corriente: {df['Amperios'].mean():.1f} A.")

# --- EXCEL PRO CON GRÁFICO ---
def generar_excel_profesional(df_reporte, planta, periodo, analisis_manual):
    output = io.BytesIO()
    if df_reporte.empty: return None
    df_rep = df_reporte.copy()
    
    # Agregar ID Técnico
    df_rep['ID_Tecnico'] = df_rep.apply(crear_id_tecnico, axis=1)
    
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb = writer.book
            ws = wb.add_worksheet('Reporte Ingeniería')
            ws.hide_gridlines(2)
            
            f_title = wb.add_format({'bold': True, 'font_size': 16, 'color': '#ffffff', 'bg_color': '#2e86c1', 'align': 'center'})
            f_sub = wb.add_format({'bold': True, 'bottom': 1})
            f_wrap = wb.add_format({'text_wrap': True, 'border': 1, 'valign': 'top'})
            f_date = wb.add_format({'num_format': 'dd/mm/yyyy', 'align': 'left'})
            
            # Encabezado
            ws.merge_range('B2:H2', f"INFORME TÉCNICO: {planta.upper()}", f_title)
            ws.write('B3', f"Periodo: {periodo}")
            ws.write('E3', f"Fecha Emisión: {pd.Timestamp.now().strftime('%d-%m-%Y')}")
            
            # Texto Análisis
            ws.write('B5', "ANÁLISIS TÉCNICO:", f_sub)
            ws.merge_range('B6:F10', analisis_manual, f_wrap)
            
            # --- AGREGAR GRÁFICO DE TORTA AL EXCEL ---
            # 1. Preparar datos (Ocultos en columnas K y L)
            df_pie = df_rep['Inversor'].value_counts().reset_index()
            df_pie.columns = ['Inversor', 'Fallas']
            ws.write('K5', 'Inversor')
            ws.write('L5', 'Fallas')
            for i, row in df_pie.iterrows():
                ws.write(5 + i + 1, 10, row['Inversor']) # Col K
                ws.write(5 + i + 1, 11, row['Fallas'])   # Col L
            
            # 2. Crear Gráfico
            chart = wb.add_chart({'type': 'pie'})
            chart.add_series({
                'name': 'Fallas por Inversor',
                'categories': ['Reporte Ingeniería', 6, 10, 6 + len(df_pie) - 1, 10], # Nombres
                'values':     ['Reporte Ingeniería', 6, 11, 6 + len(df_pie) - 1, 11], # Valores
                'data_labels': {'percentage': True},
            })
            chart.set_title({'name': 'Distribución por Inversor'})
            chart.set_style(10)
            
            # 3. Insertar gráfico en la hoja
            ws.insert_chart('G6', chart, {'x_scale': 0.9, 'y_scale': 0.9})

            # Tabla de Datos
            ws.write('B13', "DETALLE OPERATIVO:", f_sub)
            cols = ['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']
            df_export = df_rep[cols].copy()
            df_export['Fecha'] = df_export['Fecha'].dt.date # Sin hora
            
            df_export.to_excel(writer, sheet_name='Reporte Ingeniería', startrow=13, startcol=1, index=False)
            ws.set_column('B:B', 12, f_date)
            
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

# --- PESTAÑA 1 ---
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
        
        if st.form_submit_button("💾 Registrar Evento", type="primary"):
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
                st.success("Registrado."); st.rerun()

    st.markdown("### 📋 Últimos Eventos")
    df_show = st.session_state.df_cache.copy()
    if not df_show.empty:
        df_p = df_show[df_show['Planta'] == planta_sel]
        if not df_p.empty:
            for i, row in df_p.tail(5).sort_index(ascending=False).iterrows():
                id_tec = crear_id_tecnico(row)
                cols = st.columns([1, 2, 2, 1, 1, 1])
                cols[0].write(f"📅 {row['Fecha'].strftime('%d/%m')}")
                cols[1].write(f"**{row['Inversor']} > {row['Caja']}**")
                cols[2].write(f"🔌 {id_tec}")
                cols[3].write(f"⚡ **{row['Amperios']} A**")
                if row['Nota']: cols[4].info(row['Nota'])
                if cols[5].button("🗑️", key=f"del_{i}"): borrar_registro_google(i); st.rerun()
        else: st.info("Sin registros.")

# --- PESTAÑA 2 ---
with tab2:
    st.header("Laboratorio de Datos")
    df = st.session_state.df_cache
    if not df.empty:
        # --- FILTROS Y KPIS AJUSTADOS ---
        col_filtro, col_kpi = st.columns([1, 3]) # Ajuste proporción
        
        with col_filtro:
            st.markdown("⏱️ **Filtros**")
            filtro_t = st.radio("Ver:", ["Todo", "Este Mes", "Último Trimestre", "Último Semestre", "Último Año", "Mes Específico"])
            
            df_f = df[df['Planta'] == planta_sel].copy()
            df_f['ID_Tecnico'] = df_f.apply(crear_id_tecnico, axis=1)
            df_f['Equipo_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
            
            hoy = pd.Timestamp.now()
            if filtro_t == "Este Mes": df_f = df_f[df_f['Fecha'].dt.month == hoy.month]
            elif filtro_t == "Último Trimestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=90))]
            elif filtro_t == "Último Semestre": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=180))]
            elif filtro_t == "Último Año": df_f = df_f[df_f['Fecha'] >= (hoy - timedelta(days=365))]
            elif filtro_t == "Mes Específico":
                c_m, c_a = st.columns(2)
                mm = c_m.selectbox("Mes", range(1,13), index=hoy.month-1)
                aa = c_a.number_input("Año", 2023, 2030, hoy.year)
                df_f = df_f[(df_f['Fecha'].dt.month == mm) & (df_f['Fecha'].dt.year == aa)]

        with col_kpi:
            # Aquí ajustamos el ancho de las columnas para que "Equipo Crítico" no se corte
            # [1, 1, 1.5, 1] da más espacio a la 3ra columna
            k1, k2, k3, k4 = st.columns([1, 1, 1.5, 1])
            k1.metric("Fallas", len(df_f))
            k2.metric("Promedio A", f"{df_f['Amperios'].mean():.1f} A")
            
            top_eq = df_f['Equipo_Full'].mode()
            txt_critico = top_eq[0] if not top_eq.empty else "-"
            k3.metric("Equipo Crítico", txt_critico)
            
            k4.metric("Repeticiones", df_f['Equipo_Full'].value_counts().max() if not df_f.empty else 0)

        st.divider()
        st.subheader("1. Cronología de Fallas")
        if not df_f.empty:
            fig_timeline = px.scatter(df_f, x="Fecha", y="ID_Tecnico", color="Polaridad", size="Amperios", height=350)
            st.plotly_chart(fig_timeline, use_container_width=True)

        # --- TRES GRÁFICOS ---
        st.subheader("2. Distribución y Ranking")
        c1, c2, c3 = st.columns(3)
        layout_cfg = dict(margin=dict(l=10, r=10, t=30, b=10), showlegend=True, height=350)

        with c1:
            st.caption("Ranking Equipos (Hover para detalle)")
            if not df_f.empty:
                # Ranking de equipos + Lista de Strings para Hover
                df_rank = df_f.groupby('Equipo_Full').agg(
                    Fallas=('Fecha', 'count'),
                    Strings_Afectados=('ID_Tecnico', lambda x: list(x))
                ).reset_index().sort_values('Fallas', ascending=True)
                
                fig = px.bar(df_rank, x='Fallas', y='Equipo_Full', orientation='h', 
                             color='Fallas', text='Fallas',
                             hover_data=['Strings_Afectados'])
                fig.update_layout(**layout_cfg)
                st.plotly_chart(fig, use_container_width=True)

        with c2:
            st.caption("Inversores (Porcentaje)")
            if not df_f.empty:
                # Torta de Inversores
                fig = px.pie(df_f, names='Inversor', title='', hole=0.4,
                             color_discrete_sequence=px.colors.qualitative.Prism)
                fig.update_traces(textposition='inside', textinfo='percent+label')
                fig.update_layout(**layout_cfg)
                fig.update_layout(showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

        with c3:
            st.caption("Polaridad")
            if not df_f.empty:
                # Torta de Polaridad
                fig = px.pie(df_f, names='Polaridad', hole=0.4,
                             color_discrete_sequence=['#EF553B', '#636EFA'])
                fig.update_layout(**layout_cfg)
                st.plotly_chart(fig, use_container_width=True)

        st.divider()
        
        # ZONA DE ANÁLISIS
        c_ia, c_man = st.columns(2)
        txt_ia = generar_analisis_auto(df_f)
        with c_ia:
            st.info("🤖 IA"); st.write(txt_ia)
            if st.button("Copiar IA"): st.session_state['texto'] = txt_ia
        with c_man:
            st.warning("👷 Ingeniero")
            txt_final = st.text_area("Conclusiones:", value=st.session_state.get('texto', ''), height=100)

        # TABLA DE DETALLE
        st.divider()
        st.subheader("📋 Detalle de Fallas")
        df_show = df_f[['Fecha', 'ID_Tecnico', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota']].copy()
        df_show['Fecha'] = df_show['Fecha'].dt.strftime('%Y-%m-%d')
        st.dataframe(df_show.sort_values('Fecha', ascending=False), use_container_width=True, hide_index=True)

        # DESCARGA
        st.divider()
        excel = generar_excel_profesional(df_f, planta_sel, filtro_t, txt_final)
        if excel:
            st.download_button("📥 Descargar Reporte (Excel)", excel, f"Reporte_{planta_sel}.xlsx", 
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
            
    else: st.info("Sin datos.")
