import io, json, requests, pandas as pd, streamlit as st
from datetime import datetime, date, timezone
import pydeck as pdk
import time

st.set_page_config(page_title="Drone Drops — Data Offload", layout="wide")
st.title("🛰️ Drone Drops — Store & Forward Offload")

# ---------- Connection ----------
ip = st.text_input("ESP32 address", value="192.168.4.1")  # AP mode default
base = f"http://{ip}"
status = st.empty()

# ---------- Helpers to talk to ESP ----------
def fetch_info(timeout=8):
    r = requests.get(f"{base}/info", timeout=timeout)
    r.raise_for_status()
    return r.json()

def fetch_state(timeout=5):
    r = requests.get(f"{base}/state", timeout=timeout)
    r.raise_for_status()
    return r.json()

def fetch_json(last=200, timeout=10):
    r = requests.get(f"{base}/log.json", params={"last": last}, timeout=timeout)
    r.raise_for_status()
    return r.json()

def fetch_csv_bytes(timeout=30):
    r = requests.get(f"{base}/log.csv", timeout=timeout)
    r.raise_for_status()
    return r.content

def start_mission(interval_s: float, delay_s: float, step_hz: int, timeout=10):
    body = {"interval_s": float(interval_s), "delay_s": float(delay_s), "step_hz": int(step_hz)}
    r = requests.post(f"{base}/start", data=json.dumps(body), timeout=timeout)
    r.raise_for_status()
    return r.json()

def stop_mission(timeout=5):
    r = requests.post(f"{base}/stop", timeout=timeout)
    r.raise_for_status()
    return r.json()

# ---------- UI Layout ----------
topA, topB = st.columns([2, 1])
colA, colB, colC = st.columns([1, 1, 1])

with topA:
    st.caption("Tip: In AP mode, connect your PC to the drone’s Wi-Fi (e.g., DRONE_ESP32-xxxx).")

with topB:
    auto_refresh = st.checkbox("Auto-refresh while running", value=True)
    refresh_every = st.number_input("Seconds", 1, 30, 3, help="Refresh interval for state & preview")

# ---------- Connect & Preview ----------
with colA:
    if st.button("🔌 Connect & Preview"):
        try:
            info = fetch_info()
            status.success(
                f"Connected. Records: {info['records']} | File: {info['bytes']} bytes | "
                f"FW {info['fw']} | State={info.get('state')}"
            )
            data = fetch_json(last=200)
            if data:
                df = pd.DataFrame(data)
                # Convert epoch seconds to local time (Mexico City)
                df["dt"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("America/Mexico_City")
                st.subheader("Preview (last 200)")
                st.dataframe(df.sort_values("ts"), use_container_width=True, height=300)
                st.subheader("Map")
                st.pydeck_chart(pdk.Deck(
                    map_style=None,
                    initial_view_state=pdk.ViewState(
                        latitude=float(df["lat"].mean()), longitude=float(df["lon"].mean()), zoom=15
                    ),
                    layers=[
                        pdk.Layer(
                            "ScatterplotLayer",
                            data=df,
                            get_position='[lon, lat]',
                            get_radius=5,
                            pickable=True,
                        )
                    ],
                ))
            else:
                st.info("No data yet.")
        except Exception as e:
            status.error(f"Connection failed: {e}")

# ---------- Download & Clear ----------
with colB:
    if st.button("⬇️ Download CSV"):
        try:
            csv_bytes = fetch_csv_bytes()
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            st.download_button(
                "Save log.csv",
                data=csv_bytes,
                file_name=f"drops-{ts}.csv",
                mime="text/csv",
                use_container_width=True
            )
            st.success("CSV ready.")
        except Exception as e:
            status.error(f"Download failed: {e}")

with colC:
    if st.button("🧹 Clear Log (after backup)"):
        try:
            r = requests.delete(f"{base}/log", timeout=5)
            r.raise_for_status()
            st.success("Log cleared on device.")
        except Exception as e:
            status.error(f"Clear failed: {e}")

st.markdown("---")

# ---------- Start/Stop Mission ----------
left, mid, right = st.columns([1.4, 1, 1])

with left:
    st.subheader("Start mission")
    with st.form("start_form", clear_on_submit=False):
        velocity = st.number_input("Drone velocity (m/s)", min_value=0.1, max_value=100.0, value=10.0, step=0.1)
        distance = st.number_input("Drop distance (m)", min_value=0.1, max_value=1000.0, value=30.0, step=0.1)
        delay_s  = st.number_input("Arm delay (s)", min_value=0.0, max_value=120.0, value=10.0, step=1.0)
        step_hz  = st.number_input("Stepper STEP frequency (Hz)", min_value=1, max_value=50000, value=200, step=10)

        computed_interval = distance / velocity
        st.info(f"Computed interval: **{computed_interval:.2f} s** (distance / velocity)")

        start_now = st.form_submit_button("🚀 Start")
        if start_now:
            try:
                resp = start_mission(interval_s=computed_interval, delay_s=delay_s, step_hz=int(step_hz))
                st.success(f"Started: {resp}")
            except Exception as e:
                st.error(f"Start failed: {e}")

with mid:
    st.subheader("Stop mission")
    if st.button("⏹️ Stop"):
        try:
            resp = stop_mission()
            st.success(f"Stopped: {resp}")
        except Exception as e:
            st.error(f"Stop failed: {e}")

with right:
    st.subheader("Device state")
    if st.button("🔄 Refresh state"):
        try:
            s = fetch_state()
            st.json(s)
        except Exception as e:
            st.error(f"State failed: {e}")

st.markdown("---")

# ---------- Daily view (Today by default) ----------
st.subheader("History — filter by day")
day = st.date_input("Pick a day", value=date.today(),
                    help="Data is stored as epoch seconds (UTC). We’ll filter to the selected day in your local timezone.")
previewN = st.slider("Preview last N rows (for quick load)", 50, 2000, 400, step=50)

# Pull recent rows (fast) and filter by day locally
try:
    data = fetch_json(last=max(previewN, 400))  # grab a chunk; adjust higher if flights are long
    df_all = pd.DataFrame(data) if data else pd.DataFrame(columns=["ts","lat","lon","alt","drop_id","speed_mps","sats","fix_ok"])
    if not df_all.empty:
        # convert ts to local tz
        df_all["dt"] = pd.to_datetime(df_all["ts"], unit="s", utc=True).dt.tz_convert("America/Mexico_City")
        day_start = pd.Timestamp.combine(day, datetime.min.time()).tz_localize("America/Mexico_City")
        day_end   = pd.Timestamp.combine(day, datetime.max.time()).tz_localize("America/Mexico_City")
        mask = (df_all["dt"] >= day_start) & (df_all["dt"] <= day_end)
        df_day = df_all.loc[mask].copy()
    else:
        df_day = df_all
except Exception as e:
    df_day = pd.DataFrame(columns=["ts","lat","lon","alt","drop_id","speed_mps","sats","fix_ok","dt"])
    status.warning(f"Could not fetch preview JSON: {e}")

# ---------- Stats + Map + Table ----------
m1, m2, m3, m4 = st.columns(4)
with m1: st.metric("Rows (selected day)", len(df_day))
with m2: st.metric("Total fetched rows", len(df_all) if 'df_all' in locals() else 0)
with m3: st.metric("Valid GPS rows", int(df_day["fix_ok"].sum()) if not df_day.empty and "fix_ok" in df_day else 0)
with m4:
    if not df_day.empty and "speed_mps" in df_day:
        st.metric("Avg speed (m/s)", f"{df_day['speed_mps'].mean():.2f}")
    else:
        st.metric("Avg speed (m/s)", "—")

if not df_day.empty:
    st.subheader("Map (selected day)")
    # Fallback view if only one point or NaNs
    lat0 = float(df_day["lat"].mean()) if df_day["lat"].notna().any() else 0.0
    lon0 = float(df_day["lon"].mean()) if df_day["lon"].notna().any() else 0.0
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        initial_view_state=pdk.ViewState(latitude=lat0, longitude=lon0, zoom=15),
        layers=[
            pdk.Layer(
                "ScatterplotLayer",
                data=df_day,
                get_position='[lon, lat]',
                get_radius=5,
                pickable=True,
                get_fill_color='[fix_ok ? 0 : 200, fix_ok ? 150 : 50, 0]',
            )
        ],
        tooltip={"text": "Drop #{drop_id}\n{dt}\nlat={lat}\nlon={lon}\nalt={alt} m\nspeed={speed_mps} m/s\nsats={sats}\nfix_ok={fix_ok}"}
    ))

    st.subheader("Data (selected day)")
    st.dataframe(df_day.sort_values("ts"), use_container_width=True, height=350)
else:
    st.info("No rows for the selected day (in recent preview). Download CSV for full history if needed.")

# ---------- Auto refresh loop (keeps the page live during mission) ----------
if auto_refresh:
    # Use Streamlit's experimental rerun pattern
    time.sleep(refresh_every)
    st.rerun()
