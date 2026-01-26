import streamlit as st
import pandas as pd
import plotly.express as px
import io
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor PMGD Cloud Pro", layout="wide", initial_sidebar_state="expanded")

# --- RUTAS ABSOLUTAS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
CONFIG_PATH = os.path.join(BASE_DIR, "plantas_config.json")

# --- CONEXIÓN A GOOGLE SHEETS ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "DB_FUSIBLES"

def conectar_google_sheets():
    try:
        creds = None
        if os.path.exists(CREDENTIALS_PATH):
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, SCOPE)
        else:
            if "gcp_service_account" in st.secrets:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), SCOPE)
        
        if creds is None:
            st.error("⚠️ Error: No se encuentra 'credentials.json' en C:\\fusible")
            st.stop()
            
        client = gspread.authorize(creds)
        return client.open(SHEET_NAME).sheet1
    except Exception as e:
        st.error(f"Error Google: {e}")
        st.stop()

# --- GESTIÓN DE DATOS (CRUD) ---
def cargar_datos():
    sheet = conectar_google_sheets()
    try:
        data = sheet.get_all_records()
        if not data: return pd.DataFrame(columns=['Fecha', 'Planta', 'Inversor', 'Caja', 'String', 'Polaridad', 'Amperios', 'Nota'])
        df = pd.DataFrame(data)
        if 'Fecha' in df.columns: df['Fecha'] = pd.to_datetime(df['Fecha'])
        if 'Planta' not in df.columns: df['Planta'] = 'Sin Asignar'
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

def borrar_registro_google(index_dataframe):
    """Borra una fila basada en el índice del DataFrame (ajustando headers)"""
    sheet = conectar_google_sheets()
    # Fila Google = Índice DF + 2 (1 por ser base-0, 1 por el header)
    row_to_delete = index_dataframe + 2
    sheet.delete_row(row_to_delete)
    st.cache_data.clear()
    st.session_state.df_cache = cargar_datos()

# --- EXCEL PRO (XlsxWriter) ---
def generar_excel_profesional(df_reporte, planta, periodo):
    output = io.BytesIO()
    if df_reporte.empty: return None

    # Preparar datos para gráficos
    df_rep = df_reporte.copy()
    df_rep['Equipo'] = df_rep['Inversor'] + " > " + df_rep['Caja']
    
    top_eq = df_rep['Equipo'].value_counts().head(10).reset_index()
    top_eq.columns = ['Equipo', 'Fallas']
    
    pol_count = df_rep['Polaridad'].value_counts().reset_index()
    pol_count.columns = ['Polaridad', 'Cantidad']

    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            wb = writer.book
            ws_dash = wb.add_worksheet('Dashboard')
            ws_dash.hide_gridlines(2)
            
            # Formatos
            f_title = wb.add_format({'bold': True, 'font_size': 18, 'color': '#1F497D'})
            f_head = wb.add_format({'bold': True, 'bg_color': '#DCE6F1', 'border': 1})
            
            # Títulos
            ws_dash.write('B2', f"Informe: {planta} ({periodo})", f_title)
            
            # Tabla Resumen
            ws_dash.write('B5', "Total Fallas", f_head)
            ws_dash.write('C5', len(df_rep), wb.add_format({'border': 1, 'align': 'center'}))
            
            # Datos Ocultos para Gráficos
            top_eq.to_excel(writer, sheet_name='Dashboard', startrow=20, startcol=1, index=False)
            pol_count.to_excel(writer, sheet_name='Dashboard', startrow=20, startcol=5, index=False)
            
            # Gráfico Barras
            chart1 = wb.add_chart({'type': 'bar'})
            chart1.add_series({
                'name': 'Fallas',
                'categories': ['Dashboard', 21, 1, 21+len(top_eq)-1, 1],
                'values': ['Dashboard', 21, 2, 21+len(top_eq)-1, 2],
                'fill': {'color': '#1F497D'}
            })
            chart1.set_title({'name': 'Top Equipos'})
            ws_dash.insert_chart('B8', chart1)
            
            # Gráfico Torta
            chart2 = wb.add_chart({'type': 'pie'})
            chart2.add_series({
                'name': 'Polaridad',
                'categories': ['Dashboard', 21, 5, 21+len(pol_count)-1, 5],
                'values': ['Dashboard', 21, 6, 21+len(pol_count)-1, 6],
            })
            ws_dash.insert_chart('J8', chart2)
            
            # Hoja de Datos
            df_reporte.to_excel(writer, sheet_name='Bitacora', index=False)
            
    except Exception as e:
        # Fallback si falla xlsxwriter
        return None
        
    return output.getvalue()

# --- CACHÉ Y CONFIG ---
if 'df_cache' not in st.session_state:
    st.session_state.df_cache = cargar_datos()

def cargar_lista_plantas():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f: return json.load(f)
        except: return ["El Roble", "Las Rojas"]
    return ["El Roble", "Las Rojas"]

def guardar_lista_plantas(lista):
    with open(CONFIG_PATH, 'w') as f: json.dump(lista, f)

plantas_disponibles = cargar_lista_plantas()

# --- INTERFAZ ---
st.title("☁️ Gestor PMGD Pro (Google Sheets)")

if st.button("🔄 Sincronizar con Nube"):
    st.session_state.df_cache = cargar_datos()
    st.success("Actualizado.")

with st.sidebar:
    st.header("🏭 Planta Activa")
    planta_sel = st.selectbox("Selecciona:", plantas_disponibles) if plantas_disponibles else None
    
    st.markdown("---")
    with st.expander("⚙️ Administrar Plantas"):
        nueva = st.text_input("Nueva planta")
        if st.button("➕ Agregar"):
            if nueva and nueva not in plantas_disponibles:
                plantas_disponibles.append(nueva)
                guardar_lista_plantas(plantas_disponibles)
                st.rerun()

tab1, tab2 = st.tabs(["📝 Registro & Edición", "📊 Auditoría & Reportes"])

# === PESTAÑA 1: REGISTRO ===
with tab1:
    if planta_sel:
        st.markdown(f"### Nuevo Registro: **{planta_sel}**")
        with st.form("entry"):
            c1, c2, c3 = st.columns(3)
            fecha = c1.date_input("Fecha", pd.Timestamp.now())
            inv = c2.number_input("Inversor", 1, 50, 1)
            cja = c3.number_input("Caja", 1, 100, 1)
            c4, c5, c6 = st.columns(3)
            str_n = c4.number_input("String", 1, 30, 1)
            pol = c5.selectbox("Polaridad", ["Positivo (+)", "Negativo (-)"])
            nota = c6.text_input("Nota")
            
            if st.form_submit_button("💾 Guardar en Nube", type="primary"):
                df = st.session_state.df_cache
                # Check duplicados
                dup = df[(df['Planta'] == planta_sel) & (df['Fecha'] == pd.to_datetime(fecha)) & 
                         (df['Inversor'] == f"Inv-{inv}") & (df['Caja'] == f"CB-{cja}") & 
                         (df['String'] == f"Str-{str_n}")] if not df.empty else pd.DataFrame()
                
                if not dup.empty:
                    st.error("⛔ ¡Registro duplicado! Ya existe hoy.")
                else:
                    new_data = {'Fecha': pd.to_datetime(fecha), 'Planta': planta_sel, 
                                'Inversor': f"Inv-{inv}", 'Caja': f"CB-{cja}", 
                                'String': f"Str-{str_n}", 'Polaridad': pol, 'Amperios': 0, 'Nota': nota}
                    guardar_registro_nuevo(new_data)
                    st.session_state.df_cache = cargar_datos()
                    st.success("Guardado Exitoso!")
                    st.rerun()

        st.markdown("---")
        st.subheader("📋 Últimos Registros (Con opción de Borrar)")
        
        df_show = st.session_state.df_cache.copy()
        if not df_show.empty:
            # Filtramos por planta para mostrar
            df_planta = df_show[df_show['Planta'] == planta_sel]
            if not df_planta.empty:
                # Tomamos los últimos 5
                recientes = df_planta.tail(5).sort_index(ascending=False)
                
                # Encabezados
                cols = st.columns([2, 1, 1, 1, 1, 3, 1])
                headers = ["Fecha", "Inv", "Caja", "Str", "Pol", "Nota", "Borrar"]
                for col, h in zip(cols, headers): col.markdown(f"**{h}**")
                
                # Filas con botón
                for idx, row in recientes.iterrows():
                    cols = st.columns([2, 1, 1, 1, 1, 3, 1])
                    cols[0].write(row['Fecha'].strftime('%d-%m-%Y'))
                    cols[1].write(row['Inversor'])
                    cols[2].write(row['Caja'])
                    cols[3].write(row['String'])
                    cols[4].write(row['Polaridad'])
                    cols[5].write(row['Nota'])
                    # EL BOTÓN DE BORRAR REAL
                    if cols[6].button("🗑️", key=f"del_{idx}"):
                        borrar_registro_google(idx)
                        st.warning("Registro eliminado de la Nube.")
                        st.rerun()
            else:
                st.info("No hay registros en esta planta.")
        else:
            st.info("Base de datos vacía.")

# === PESTAÑA 2: REPORTES ===
with tab2:
    st.header("Panel de Inteligencia")
    df = st.session_state.df_cache
    if not df.empty:
        c1, c2 = st.columns(2)
        p_filter = c1.selectbox("Planta:", ["Todas"] + list(df['Planta'].unique()))
        df_f = df if p_filter == "Todas" else df[df['Planta'] == p_filter]
        
        periodo = c2.selectbox("Periodo:", ['Historico', 'Mensual', 'Anual'])
        
        # Filtro de fecha simple (Mejora visual)
        if periodo == 'Mensual':
            df_f = df_f[df_f['Fecha'].dt.month == pd.Timestamp.now().month]
        
        st.markdown("---")
        
        # KPIs
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Fallas", len(df_f))
        k2.metric("Positivos (+)", len(df_f[df_f['Polaridad'] == "Positivo (+)"]))
        k3.metric("Negativos (-)", len(df_f[df_f['Polaridad'] == "Negativo (-)"]))
        
        # Top Falla
        if not df_f.empty:
            df_f['ID_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
            top = df_f['ID_Full'].mode()[0] if not df_f['ID_Full'].empty else "-"
            k4.metric("Peor Equipo", top)
            
            # --- SELECCIÓN DE GRÁFICOS ---
            st.subheader("Visualización")
            tipo = st.radio("Modo:", ["📊 Barras Detalle", "🔥 Mapa de Calor (Heatmap)"], horizontal=True)
            
            if tipo == "📊 Barras Detalle":
                g1, g2 = st.columns(2)
                with g1:
                    df_rank = df_f.groupby('ID_Full').size().reset_index(name='Count').sort_values('Count')
                    fig = px.bar(df_rank, x='Count', y='ID_Full', orientation='h', title="Ranking Equipos")
                    st.plotly_chart(fig, use_container_width=True)
                with g2:
                    fig2 = px.pie(df_f, names='Polaridad', title="Polaridad", color_discrete_sequence=['#ef553b', '#636efa'])
                    st.plotly_chart(fig2, use_container_width=True)
            
            else:
                st.info("Mapa de Intensidad: Muestra qué cajas fallan más por Inversor.")
                df_heat = df_f.groupby(['Inversor', 'Caja']).size().reset_index(name='Fallas')
                fig_heat = px.density_heatmap(df_heat, x="Caja", y="Inversor", z="Fallas", text_auto=True, color_continuous_scale="Viridis")
                st.plotly_chart(fig_heat, use_container_width=True)

            # --- DESCARGA EXCEL PRO ---
            st.markdown("---")
            excel_bytes = generar_excel_profesional(df_f, p_filter, periodo)
            if excel_bytes:
                st.download_button("📥 Descargar Informe Técnico (Excel Pro)", excel_bytes, f"Reporte_{p_filter}.xlsx", 
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    else:
        st.info("Sin datos.")