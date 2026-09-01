"""Single Streamlit entry point for the Data Generation application."""

import streamlit as st

from app.health import get_health_status

st.set_page_config(page_title="Data Generation", page_icon="🧪", layout="wide")

st.sidebar.title("Data Generation")
view = st.sidebar.radio("Main navigation", ("Data Generation", "Talk to your data"))
status = get_health_status()
if status.app_ready and status.database_ready:
    st.sidebar.success("System ready")
elif status.app_ready:
    st.sidebar.warning("Application ready; database unavailable")
else:
    st.sidebar.error("Application configuration is invalid")

st.title(view)
if view == "Data Generation":
    st.info("DDL upload and synthetic generation will be available in Step 5.")
else:
    st.info("Natural-language querying will be available in Step 7.")
