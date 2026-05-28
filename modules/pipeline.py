"""Tab 2 — Pipeline (Kanban board).

Sprint 3 build target. Stub renders the planned column layout so the
3-tab navigation is complete end-to-end in Sprint 1.
"""
from __future__ import annotations

import streamlit as st

STAGES = ["Screening", "LOI Draft", "Negotiation", "Due Diligence", "Signing", "Live"]


def render(brand: dict) -> None:
    st.subheader("📋 Pipeline")
    st.caption(f"Active {brand['brand']} sites from initial screening to live store.")

    st.info("⚙️ **Sprint 3.** Kanban board, site detail view, broker log, milestone alerts.")

    cols = st.columns(len(STAGES))
    for i, stage in enumerate(STAGES):
        with cols[i]:
            st.markdown(
                f"<div style='background:#F5F5F5;padding:8px 10px;"
                f"border-radius:6px;font-weight:600;text-align:center;"
                f"border-top:3px solid #00C853;'>{stage}</div>",
                unsafe_allow_html=True,
            )
            st.caption("0 sites")
