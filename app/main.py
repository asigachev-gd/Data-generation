"""Single Streamlit entry point for the Data Generation application."""

import streamlit as st
from pydantic import ValidationError

from app.config import get_settings
from app.health import get_health_status
from app.ui import render_data_generation

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
    try:
        render_data_generation(st, settings=get_settings())
    except ValidationError:
        st.error("Generation is unavailable until application configuration is valid.")
else:
    st.info("Natural-language querying will be available in Step 7.")
