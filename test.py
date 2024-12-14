from google.cloud import bigquery
from google.oauth2 import service_account
import google.auth
import os

def authenticate_and_list_datasets():
    try:
        # Path to the newly downloaded service account key
        service_account_path = '/mnt/c/Users/priya/apac/AI4Impact2024/fastapi-backend/YOUR_NEW_SERVICE_ACCOUNT_KEY.json'
        
        # Explicitly load credentials
        credentials = service_account.Credentials.from_service_account_file(
            service_account_path, 
            scopes=[
                'https://www.googleapis.com/auth/bigquery',
                'https://www.googleapis.com/auth/cloud-platform'
            ]
        )

        # Create client with explicit credentials and project
        client = bigquery.Client(
            credentials=credentials,
            project=credentials.project_id
        )

        # Attempt to list datasets
        datasets = list(client.list_datasets())

        print(f"Connected to project: {client.project}")
        if datasets:
            print("Datasets:")
            for dataset in datasets:
                print(f"- {dataset.dataset_id}")
        else:
            print("No datasets found in the project.")

    except Exception as e:
        print("Detailed Error:")
        print(f"Error Type: {type(e)}")
        print(f"Error Message: {str(e)}")
        import traceback
        traceback.print_exc()

# Set environment variable (optional)
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/mnt/c/Users/priya/apac/AI4Impact2024/fastapi-backend/YOUR_NEW_SERVICE_ACCOUNT_KEY.json'

# Run the function
authenticate_and_list_datasets()