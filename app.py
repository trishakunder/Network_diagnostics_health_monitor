import socket
import sqlite3
import time
from datetime import datetime

import pandas as pd
import streamlit as st

DB_FILE = "network_monitor.db"
WARNING_MS = 500
CRITICAL_MS = 1500

st.set_page_config(
    page_title="Network Health Monitor",
    page_icon="🌐",
    layout="wide"
)


def initialize_db():
    with sqlite3.connect(DB_FILE) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS monitoring_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                dns_status TEXT NOT NULL,
                tcp_status TEXT NOT NULL,
                dns_latency_ms REAL,
                tcp_latency_ms REAL,
                resolved_ips TEXT,
                diagnostic_details TEXT
            )
        """)


def dns_check(host):
    start = time.perf_counter()
    try:
        results = socket.getaddrinfo(
            host, None, type=socket.SOCK_STREAM
        )
        ips = sorted({item[4][0] for item in results})
        return "SUCCESS", round(
            (time.perf_counter() - start) * 1000, 2
        ), ", ".join(ips)
    except socket.gaierror as exc:
        return "FAILED", round(
            (time.perf_counter() - start) * 1000, 2
        ), str(exc)


def tcp_check(host, port):
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=5):
            latency = round(
                (time.perf_counter() - start) * 1000, 2
            )
        return "SUCCESS", latency, f"TCP port {port} reachable"
    except OSError as exc:
        latency = round(
            (time.perf_counter() - start) * 1000, 2
        )
        return "FAILED", latency, str(exc)


def diagnose(dns_status, tcp_status, latency):
    if dns_status != "SUCCESS":
        return (
            "DNS FAILURE",
            "Check hostname spelling, DNS configuration, "
            "and network connectivity."
        )

    if tcp_status != "SUCCESS":
        return (
            "TCP FAILURE",
            "Check service availability, destination port, "
            "firewall rules, and network route."
        )

    if latency >= CRITICAL_MS:
        return (
            "HIGH LATENCY",
            "Repeat measurements and investigate possible "
            "routing delays or destination responsiveness."
        )

    if latency >= WARNING_MS:
        return (
            "LATENCY WARNING",
            "Compare repeated measurements and investigate "
            "possible network delays."
        )

    return (
        "HEALTHY",
        "DNS and TCP checks succeeded within the configured threshold."
    )


def run_check(host, port):
    dns_status, dns_ms, ips = dns_check(host)
    tcp_status, tcp_ms, details = tcp_check(host, port)
    status, advice = diagnose(dns_status, tcp_status, tcp_ms)

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "host": host,
        "port": port,
        "dns_status": dns_status,
        "tcp_status": tcp_status,
        "dns_latency_ms": dns_ms,
        "tcp_latency_ms": tcp_ms,
        "resolved_ips": ips,
        "diagnostic_details": details,
        "diagnosis": status,
        "recommendation": advice
    }

    with sqlite3.connect(DB_FILE) as con:
        con.execute("""
            INSERT INTO monitoring_results (
                timestamp, host, port, dns_status, tcp_status,
                dns_latency_ms, tcp_latency_ms, resolved_ips,
                diagnostic_details
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result["timestamp"], host, port, dns_status, tcp_status,
            dns_ms, tcp_ms, ips, details
        ))

        con.execute("""
            CREATE TABLE IF NOT EXISTS diagnostic_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, host TEXT, port INTEGER,
                status TEXT, latency_ms REAL,
                diagnosis TEXT, recommendation TEXT
            )
        """)

        con.execute("""
            INSERT INTO diagnostic_results (
                timestamp, host, port, status, latency_ms,
                diagnosis, recommendation
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            result["timestamp"], host, port, status, tcp_ms,
            status, advice
        ))

    return result


initialize_db()

st.title("🌐 Network Diagnostics & Health Monitor")
st.caption(
    "DNS resolution • TCP connectivity • Performance history "
    "• Rule-based diagnostics"
)

with st.sidebar:
    st.header("Monitor configuration")
    st.write("Enter one host and port per line.")
    host_text = st.text_area(
        "Hosts",
        "google.com:443\nexample.com:443\ncloudflare.com:443"
    )
    st.caption("Only check hosts and services you are authorized to test.")

    run_button = st.button(
        "Run network checks",
        type="primary",
        use_container_width=True
    )

targets = []
for line in host_text.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        host, port_text = line.rsplit(":", 1)
        port = int(port_text)
        if not host or not 1 <= port <= 65535:
            raise ValueError
        targets.append((host, port))
    except ValueError:
        st.sidebar.error(f"Invalid entry: {line}")

if run_button:
    if not targets:
        st.error("Enter at least one valid host:port.")
    else:
        progress = st.progress(0)
        for index, (host, port) in enumerate(targets, start=1):
            try:
                result = run_check(host, port)
                st.write(
                    f"**{host}:{port}** — {result['diagnosis']} "
                    f"— TCP {result['tcp_latency_ms']} ms"
                )
            except Exception as exc:
                st.error(f"Could not check {host}:{port}: {exc}")
            progress.progress(index / len(targets))
        st.success("Checks completed and saved to SQLite.")

with sqlite3.connect(DB_FILE) as con:
    history = pd.read_sql_query("""
        SELECT * FROM monitoring_results ORDER BY id DESC
    """, con)

    diagnostics = pd.read_sql_query("""
        SELECT * FROM diagnostic_results ORDER BY id DESC
    """, con)

if history.empty:
    st.info("Run network checks from the sidebar to populate the dashboard.")
else:
    latest = (
        history.sort_values("id")
        .groupby(["host", "port"], as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )

    total = len(latest)
    up = (
        (latest["dns_status"] == "SUCCESS")
        & (latest["tcp_status"] == "SUCCESS")
    ).sum()
    failed = total - up

    c1, c2, c3 = st.columns(3)
    c1.metric("Configured services", total)
    c2.metric("Currently reachable", int(up))
    c3.metric("Failed checks", int(failed))

    overview_tab, performance_tab, diagnostics_tab, history_tab = st.tabs([
        "Overview", "Performance", "Diagnostics", "History"
    ])

    with overview_tab:
        st.subheader("Latest status by service")
        view = latest[[
            "host", "port", "dns_status", "tcp_status",
            "tcp_latency_ms", "timestamp"
        ]].copy()
        view["status"] = view.apply(
            lambda row: "UP"
            if row["dns_status"] == "SUCCESS"
            and row["tcp_status"] == "SUCCESS"
            else "DOWN",
            axis=1
        )
        st.dataframe(view, use_container_width=True, hide_index=True)

    with performance_tab:
        st.subheader("TCP connection setup time")
        selected_host = st.selectbox(
            "Choose a host",
            sorted(history["host"].unique())
        )
        selected = history[history["host"] == selected_host].copy()
        selected["timestamp"] = pd.to_datetime(
            selected["timestamp"], errors="coerce"
        )
        selected = selected.dropna(subset=["timestamp"])
        selected = selected.sort_values("timestamp")

        if not selected.empty:
            chart_data = selected.set_index("timestamp")[
                ["tcp_latency_ms", "dns_latency_ms"]
            ]
            st.line_chart(chart_data)
            st.caption(
                "These are DNS and TCP connection setup times, "
                "not ICMP ping times or webpage download times."
            )

    with diagnostics_tab:
        st.subheader("Troubleshooting results")
        if diagnostics.empty:
            st.info("No diagnostic results yet.")
        else:
            st.dataframe(
                diagnostics[[
                    "timestamp", "host", "port", "status",
                    "latency_ms", "recommendation"
                ]],
                use_container_width=True,
                hide_index=True
            )

    with history_tab:
        st.subheader("Monitoring history")
        st.dataframe(
            history.drop(columns=["id"], errors="ignore"),
            use_container_width=True,
            hide_index=True
        )
        st.download_button(
            "Download history as CSV",
            history.to_csv(index=False).encode("utf-8"),
            file_name="network_monitor_history.csv",
            mime="text/csv"
        )
