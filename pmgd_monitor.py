import streamlit as st
import pandas as pd
import plotly.express as px
import io
import json
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="Gestor PMGD Pro", layout="wide", initial_sidebar_state="expanded")

# --- CONEXIÓN INTELIGENTE (LOCAL VS NUBE) ---
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_NAME = "DB_FUSIBLES"

def conectar_google_sheets():
    """Conecta a Google Sheets detectando si estamos en PC local o en Nube"""
    creds = None
    local_file = "credentials.json"
    
    if os.path.exists(local_file):
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_name(local_file, SCOPE)
        except Exception as e:
            st.error(f"Error leyendo archivo local: {e}")
            
    if creds is None:
        try:
            if "gcp_service_account" in st.secrets:
                creds_dict = dict(st.secrets["gcp_service_account"])
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
        except Exception as e:
            st.error(f"⚠️ Error en Secrets de Streamlit: {e}")
            st.stop()
    
    if creds is None:
        st.error("🚫 NO SE ENCUENTRA LA LLAVE DE ACCESO")
        st.warning("Revisa credentials.json (PC) o Secrets (Nube).")
        st.stop()
            
    try:
        client = gspread.authorize(creds)
        return client.open(SHEET_NAME).sheet1
    except Exception as e:
        st.error(f"Error conectando a Google Drive: {e}")
        st.stop()

# --- GESTIÓN DE DATOS ---
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
    try:
        sheet = conectar_google_sheets()
        # Cálculo de fila: Index del DF + 2 (1 por base-0 y 1 por encabezado)
        row_to_delete = index_dataframe + 2
        
        # --- CORRECCIÓN AQUÍ: Usamos delete_rows (plural) ---
        sheet.delete_rows(row_to_delete)
        
        st.cache_data.clear()
        st.session_state.df_cache = cargar_datos()
        st.toast("✅ Registro eliminado correctamente", icon="🗑️")
    except Exception as e:
        st.error(f"No se pudo borrar: {e}")

# --- EXCEL PRO ---
def generar_excel_profesional(df_reporte, planta, periodo):
    output = io.BytesIO()
    if df_reporte.empty: return None
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
            f_title = wb.add_format({'bold': True, 'font_size': 18, 'color': '#1F497D'})
            f_head = wb.add_format({'bold': True, 'bg_color': '#DCE6F1', 'border': 1})
            ws_dash.write('B2', f"Informe: {planta} ({periodo})", f_title)
            ws_dash.write('B5', "Total Fallas", f_head)
            ws_dash.write('C5', len(df_rep), wb.add_format({'border': 1, 'align': 'center'}))
            top_eq.to_excel(writer, sheet_name='Dashboard', startrow=20, startcol=1, index=False)
            pol_count.to_excel(writer, sheet_name='Dashboard', startrow=20, startcol=5, index=False)
            chart1 = wb.add_chart({'type': 'bar'})
            chart1.add_series({'name': 'Fallas', 'categories': ['Dashboard', 21, 1, 21+len(top_eq)-1, 1], 'values': ['Dashboard', 21, 2, 21+len(top_eq)-1, 2], 'fill': {'color': '#1F497D'}})
            ws_dash.insert_chart('B8', chart1)
            chart2 = wb.add_chart({'type': 'pie'})
            chart2.add_series({'name': 'Polaridad', 'categories': ['Dashboard', 21, 5, 21+len(pol_count)-1, 5], 'values': ['Dashboard', 21, 6, 21+len(pol_count)-1, 6]})
            ws_dash.insert_chart('J8', chart2)
            df_reporte.to_excel(writer, sheet_name='Bitacora', index=False)
    except: return None
    return output.getvalue()

# --- CACHÉ ---
if 'df_cache' not in st.session_state:
    st.session_state.df_cache = cargar_datos()

PLANTAS_DEFAULT = ["El Roble", "Las Rojas"]
def cargar_lista_plantas():
    if os.path.exists("plantas_config.json"):
        try:
            with open("plantas_config.json", 'r') as f: return json.load(f)
        except: return PLANTAS_DEFAULT
    return PLANTAS_DEFAULT

def guardar_lista_plantas(lista):
    with open("plantas_config.json", 'w') as f: json.dump(lista, f)

plantas_disponibles = cargar_lista_plantas()

# --- INTERFAZ ---
st.title("☁️ Gestor PMGD Pro")

if st.button("🔄 Sincronizar"):
    st.session_state.df_cache = cargar_datos()
    st.success("OK")

with st.sidebar:
    st.header("🏭 Planta Activa")
    planta_sel = st.selectbox("Selecciona:", plantas_disponibles)
    st.markdown("---")
    with st.expander("⚙️ Admin Plantas"):
        nueva = st.text_input("Nueva planta")
        if st.button("➕"):
            if nueva and nueva not in plantas_disponibles:
                plantas_disponibles.append(nueva)
                guardar_lista_plantas(plantas_disponibles)
                st.rerun()

tab1, tab2 = st.tabs(["📝 Registro", "📊 Reportes"])

with tab1:
    if planta_sel:
        st.subheader(f"Registro: {planta_sel}")
        with st.form("entry"):
            c1, c2, c3 = st.columns(3)
            fecha = c1.date_input("Fecha", pd.Timestamp.now())
            inv = c2.number_input("Inversor", 1, 50, 1)
            cja = c3.number_input("Caja", 1, 100, 1)
            c4, c5, c6 = st.columns(3)
            str_n = c4.number_input("String", 1, 30, 1)
            pol = c5.selectbox("Polaridad", ["Positivo (+)", "Negativo (-)"])
            nota = c6.text_input("Nota")
            
            if st.form_submit_button("💾 Guardar", type="primary"):
                df = st.session_state.df_cache
                dup = df[(df['Planta'] == planta_sel) & (df['Fecha'] == pd.to_datetime(fecha)) & 
                         (df['Inversor'] == f"Inv-{inv}") & (df['Caja'] == f"CB-{cja}") & 
                         (df['String'] == f"Str-{str_n}")] if not df.empty else pd.DataFrame()
                
                if not dup.empty:
                    st.error("⛔ Duplicado")
                else:
                    new_data = {'Fecha': pd.to_datetime(fecha), 'Planta': planta_sel, 
                                'Inversor': f"Inv-{inv}", 'Caja': f"CB-{cja}", 
                                'String': f"Str-{str_n}", 'Polaridad': pol, 'Amperios': 0, 'Nota': nota}
                    guardar_registro_nuevo(new_data)
                    st.session_state.df_cache = cargar_datos()
                    st.success("Guardado!")
                    st.rerun()

        st.markdown("---")
        st.subheader("Últimos Registros")
        df_show = st.session_state.df_cache.copy()
        if not df_show.empty:
            df_p = df_show[df_show['Planta'] == planta_sel]
            if not df_p.empty:
                recientes = df_p.tail(5).sort_index(ascending=False)
                cols = st.columns([2, 1, 1, 1, 1, 3, 1])
                headers = ["Fecha", "Inv", "Caja", "Str", "Pol", "Nota", "Borrar"]
                for col, h in zip(cols, headers): col.markdown(f"**{h}**")
                for idx, row in recientes.iterrows():
                    cols = st.columns([2, 1, 1, 1, 1, 3, 1])
                    cols[0].write(row['Fecha'].strftime('%d-%m-%Y'))
                    cols[1].write(row['Inversor'])
                    cols[2].write(row['Caja'])
                    cols[3].write(row['String'])
                    cols[4].write(row['Polaridad'])
                    cols[5].write(row['Nota'])
                    if cols[6].button("🗑️", key=f"del_{idx}"):
                        borrar_registro_google(idx)
                        st.rerun()
            else: st.info("Sin registros.")
        else: st.info("Vacío.")

with tab2:
    st.header("Inteligencia")
    df = st.session_state.df_cache
    if not df.empty:
        c1, c2 = st.columns(2)
        p_filter = c1.selectbox("Planta:", ["Todas"] + list(df['Planta'].unique()))
        df_f = df if p_filter == "Todas" else df[df['Planta'] == p_filter]
        periodo = c2.selectbox("Periodo:", ['Historico', 'Mensual'])
        if periodo == 'Mensual': df_f = df_f[df_f['Fecha'].dt.month == pd.Timestamp.now().month]
        
        k1, k2, k3 = st.columns(3)
        k1.metric("Total", len(df_f))
        k2.metric("Pos (+)", len(df_f[df_f['Polaridad'] == "Positivo (+)"]))
        k3.metric("Neg (-)", len(df_f[df_f['Polaridad'] == "Negativo (-)"]))
        
        if not df_f.empty:
            df_f['ID_Full'] = df_f['Inversor'] + " > " + df_f['Caja']
            tipo = st.radio("Vista:", ["📊 Barras", "🔥 Mapa Calor"], horizontal=True)
            if tipo == "📊 Barras":
                g1, g2 = st.columns(2)
                with g1:
                    df_rank = df_f.groupby('ID_Full').size().reset_index(name='C').sort_values('C')
                    st.plotly_chart(px.bar(df_rank, x='C', y='ID_Full', orientation='h'), use_container_width=True)
                with g2:
                    st.plotly_chart(px.pie(df_f, names='Polaridad', color_discrete_sequence=['#ef553b', '#636efa']), use_container_width=True)
            else:
                df_h = df_f.groupby(['Inversor', 'Caja']).size().reset_index(name='Fallas')
                st.plotly_chart(px.density_heatmap(df_h, x="Caja", y="Inversor", z="Fallas", text_auto=True), use_container_width=True)
            
            excel_bytes = generar_excel_profesional(df_f, p_filter, periodo)
            if excel_bytes:
                st.download_button("📥 Excel Pro", excel_bytes, "reporte.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    else: st.info("Sin datos.")