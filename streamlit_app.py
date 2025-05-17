import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime, timedelta

# Configs
MAPBOX_TOKEN = "pk.eyJ1IjoidGFyM3EiLCJhIjoiY21hcWljdGltMDBjazJscXI5bmh0dzYyeiJ9.6cDhFkkRaurZpRbkRuuRrw"
MAPBOX_DIRECTIONS = "https://api.mapbox.com/directions/v5/mapbox/driving"
MAPBOX_OPTIMIZED_TRIPS = "https://api.mapbox.com/optimized-trips/v1/mapbox/driving"
PICKUP_TIME_PER_ORDER = 2  # in minutes
HANDOVER_TIME_PER_ORDER = 10  # in minutes
ORDER_CUTOFF_MINUTES = 30

st.set_page_config(page_title="Auto Assign & Route", layout="wide")
st.title("🚚 Parkview Auto Assign & Route")

uploaded_file = st.file_uploader("Upload your delivery Excel file", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file)
    df['date_added'] = pd.to_datetime(df['date_added'])
    drivers = df[['driver_name', 'Driver Shift']].drop_duplicates()

    def get_travel_info(start_lat, start_lng, end_lat, end_lng):
        url = f"{MAPBOX_DIRECTIONS}/{start_lng},{start_lat};{end_lng},{end_lat}"
        params = {"access_token": MAPBOX_TOKEN, "geometries": "geojson"}
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            route = data['routes'][0]
            return route['duration'] / 60, route['distance'] / 1000  # min, km
        return float("inf"), float("inf")

    def get_optimized_route(pickup_lat, pickup_lng, destinations):
        coord_str = f"{pickup_lng},{pickup_lat};" + ";".join([f"{lng},{lat}" for lat, lng in destinations])
        url = f"{MAPBOX_OPTIMIZED_TRIPS}/{coord_str}"
        params = {
            "access_token": MAPBOX_TOKEN,
            "geometries": "geojson",
            "roundtrip": "false",
            "source": "first",
            "destination": "last"
        }
        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if 'trips' in data and len(data['trips']) > 0:
                return data['trips'][0], data['waypoints']
        return None, None

    assignments = []

    for slot in df['Time Slot'].unique():
        slot_orders = df[df['Time Slot'] == slot].copy()
        shift_drivers = drivers[drivers['Driver Shift'] == slot]
        if shift_drivers.empty:
            shift_drivers = drivers[drivers['driver_name'].str.contains("Spare")]

        batch_size = 3
        batches = [slot_orders.iloc[i:i+batch_size] for i in range(0, len(slot_orders), batch_size)]

        driver_index = 0
        for batch in batches:
            if driver_index >= len(shift_drivers):
                break
            driver = shift_drivers.iloc[driver_index]
            driver_name = driver['driver_name']
            used_spare = "Yes" if "Spare" in driver_name else "No"

            destinations = list(zip(batch['Delivery lat'], batch['Delivery lng']))
            optimized_trip, waypoints = get_optimized_route(batch.iloc[0]['Pickup Lat'], batch.iloc[0]['Pickup Lng'], destinations)
            if not optimized_trip:
                continue

            start_time = batch['date_added'].min() + timedelta(minutes=ORDER_CUTOFF_MINUTES)
            pickup_time_total = PICKUP_TIME_PER_ORDER * len(batch)
            prev_lat, prev_lng = batch.iloc[0]['Pickup Lat'], batch.iloc[0]['Pickup Lng']
            cumulative_time = start_time + timedelta(minutes=pickup_time_total)

            for point in sorted(waypoints, key=lambda x: x['waypoint_index']):
                idx = point['waypoint_index']
                order = batch.iloc[idx]
                lat, lng = order['Delivery lat'], order['Delivery lng']
                travel_minutes, distance_km = get_travel_info(prev_lat, prev_lng, lat, lng)
                arrival_time = cumulative_time + timedelta(minutes=travel_minutes + HANDOVER_TIME_PER_ORDER)
                slot_end_hour = int(slot.split('-')[1].replace("PM", "15").replace("AM", "00"))
                slot_end_time = datetime.combine(order['date_added'].date(), datetime.min.time()) + timedelta(hours=slot_end_hour)
                sla_status = "Success" if arrival_time <= slot_end_time else "Failed"
                assignments.append({
                    "order_id": order['order_id'],
                    "driver_name": driver_name,
                    "used_spare": used_spare,
                    "pickup_time": cumulative_time.strftime("%H:%M"),
                    "travel_time_min": round(travel_minutes, 2),
                    "distance_km": round(distance_km, 2),
                    "arrival_time": arrival_time.strftime("%H:%M"),
                    "slot": slot,
                    "sla_status": sla_status
                })
                prev_lat, prev_lng = lat, lng
                cumulative_time = arrival_time
            driver_index += 1

    if assignments:
        result_df = pd.DataFrame(assignments)
        st.success("Assignment completed. Preview below:")
        st.dataframe(result_df)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            result_df.to_excel(writer, index=False)
        st.download_button("📄 Download Assigned Orders", output.getvalue(), file_name="assigned_orders.xlsx")
    else:
        st.warning("No assignments made. Please check your data.")
