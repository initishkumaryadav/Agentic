import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from src.ui_common import DB_NAME, odbc_url  # noqa: E402  (after .env is loaded)

data_path = project_root / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"

db_user = os.getenv("SQL_ADMIN_USER")
db_password = os.getenv("SQL_ADMIN_PASSWORD")
if not (db_user and db_password and DB_NAME):
    sys.exit("Set SQL_ADMIN_USER, SQL_ADMIN_PASSWORD and DB_NAME in .env first.")

# 1. Load the raw dataset
print(f"Loading CSV from {data_path}...")
df = pd.read_csv(data_path)

# 2. Rename to the legacy column names the SQL view expects
legacy_mapping = {
    'timestamp': 'TS_UTC',
    'vehicle_gps_latitude': 'V_LAT',
    'vehicle_gps_longitude': 'V_LON',
    'iot_temperature': 'IOT_TEMP_VAL_C',
    'cargo_condition_status': 'CGO_COND_CD',
    'risk_classification': 'RISK_CLS_TXT',
    'delay_probability': 'DELAY_PROB_DEC',
    'port_congestion_level': 'PRT_CNG_LVL',
    'route_risk_level': 'RT_RSK_IDX'
}
df_legacy = df[list(legacy_mapping.keys())].rename(columns=legacy_mapping)
df_legacy['SYS_INGEST_FLAG'] = 'Y'

# 3. Connect with the same connection settings as the app (SQL_ENCRYPT, SQL_TRUST_SERVER_CERT)
print("Connecting to legacy MSSQL Database...")
engine = create_engine(odbc_url(db_user, db_password))

# 4. Ingest data into the messy table name
table_name = 'Raw_DATA'
print(f"Ingesting into {table_name}. This may take a minute...")
df_legacy.to_sql(table_name, engine, if_exists='replace', index=False, schema='dbo')

print("Legacy data ingestion complete!")
