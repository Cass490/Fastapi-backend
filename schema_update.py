from google.cloud import bigquery
from google.oauth2 import service_account
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv('key.env')

# Service Account Setup
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, 
    scopes=["https://www.googleapis.com/auth/bigquery"]
)

# Initialize BigQuery client
client = bigquery.Client(
    credentials=credentials, 
    project='useful-melody-444213-m6'
)

def update_medical_terms_schema():
    # Get the current table
    table = client.get_table('useful-melody-444213-m6.tbird_resources.dbtable')

    # Define new schema fields
    new_schema = table.schema + [
    
         # New Performance and Tracking Rows
    bigquery.SchemaField("Query_Count", "INTEGER"),  # How many times term was queried
    bigquery.SchemaField("Last_Queried", "TIMESTAMP"),  # Most recent query time
    bigquery.SchemaField("Average_Concept_Coverage", "FLOAT"),  # Average quality of explanation
    bigquery.SchemaField("API_Tokens_Used", "INTEGER"),  # Tokens consumed generating explanation
    bigquery.SchemaField("Source", "STRING")  # Where explanation originated (UMLS/Gemini/Manual)

    ]

    # Update the table schema
    table.schema = new_schema
    updated_table = client.update_table(table, ["schema"])

    print("Table schema updated successfully.")

# Run the update
update_medical_terms_schema()