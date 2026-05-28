"""Tab 3 — Portfolio (lease calendar, alerts, utilization).

Sprint 3 build target. Stub renders a portfolio overview seeded with the
four real On Switzerland locations so the page is meaningful from day one.
"""
from __future__ import annotations

import streamlit as st
import pandas as pd


def render(brand: dict) -> None:
    st.subheader("🏢 Portfolio")
    st.caption(f"{brand['brand']} owned & partner locations · lease events · utilization.")

    st.info("⚙️ **Sprint 3.** 24-month lease calendar, break-option / renewal / indexation alerts, utilization dashboard, quarterly reporting export.")

    stores = brand.get("existing_stores", [])
    if stores:
        df = pd.DataFrame(stores)[["name", "address", "type"]]
        df.columns = ["Name", "Address", "Type"]
        st.markdown("##### Current portfolio (seed data)")
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.caption(
            "Lease events, areas, rents, utilization and risk overlays "
            "(incl. BAFU flood-risk per location) wired in Sprint 3."
        )
