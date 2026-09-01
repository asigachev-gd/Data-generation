"""Single Streamlit entry point for the Data Generation application."""

import streamlit as st
from pydantic import ValidationError

from app.config import get_settings
from app.health import get_health_status
from app.observability import configure_logging, configure_telemetry
from app.ui import render_data_generation, render_talk_to_data

st.set_page_config(page_title="Data Generation", page_icon="🧪", layout="wide")
configure_logging()

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
        settings = get_settings()
        configure_telemetry(settings)
        render_data_generation(st, settings=settings)
    except ValidationError:
        st.error("Generation is unavailable until application configuration is valid.")
else:
    try:
        settings = get_settings()
        configure_telemetry(settings)
        render_talk_to_data(st, settings=settings)
    except ValidationError:
        st.error("Querying is unavailable until application configuration is valid.")
