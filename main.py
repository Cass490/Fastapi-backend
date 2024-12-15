from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import bigquery
from google.oauth2 import service_account
import os
from dotenv import load_dotenv
from datetime import datetime
import time
from gemini import query_gemini, validate_response_coverage, fetch_umls_data 
import spacy

# Load environment variables
load_dotenv('key.env')

# Initialize FastAPI app
app = FastAPI()

# Service Account Setup
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')

# Create credentials for BigQuery
bq_credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/bigquery"]
)

# Initialize BigQuery client
bq_client = bigquery.Client(
    credentials=bq_credentials, 
    project='useful-melody-444213-m6'  
)

def extract_medical_term(input_text):
    """
    Extract the medical term from a given input text using spaCy.
    """
    try:
        try:
            nlp = spacy.load('en_core_sci_md')  # Medical model
        except OSError:
            nlp = spacy.load('en_core_web_sm')  # Fallback to general model

        # Process the text
        doc = nlp(input_text)
        
        # Strategy 1: Look for proper nouns or nouns that might represent medical conditions
        medical_terms = [
            token.text for token in doc 
            if token.pos_ in ['PROPN', 'NOUN'] and len(token.text) > 2
        ]
        
        # Strategy 2: If no medical terms found, fall back to the longest meaningful word
        if not medical_terms:
            medical_terms = [
                token.text for token in doc 
                if len(token.text) > 3 and not token.is_stop
            ]
        
        # Return the most likely medical term (first in the list)
        return medical_terms[0] if medical_terms else input_text

    except Exception as e:
        print(f"Error extracting medical term: {e}")
        return input_text

# Pydantic model for input validation

class MedicalTermInput(BaseModel):
    term: str
    simplified_explanation: Optional[str] = None
    additional_details: Optional[dict] = None

@app.get("/medical-explanation")
def get_medical_explanation(term: str):
    """
    Endpoint for generating user-friendly medical explanations.
    Always generates a Gemini response and updates the database.
    """
    try:
        # Extract medical term
        processed_term = extract_medical_term(term)
        
        # Default fallback response structure
        default_response = {
            "term": processed_term,
            "simple_explanation": "We couldn't find detailed information about this medical term. Medical conditions can be complex and unique.",
            "signs": [
                "Limited information available",
                "Recommend consulting a healthcare professional"
            ],
            "care_tips": [
                "Verify the spelling of the medical term",
                "Consult with a healthcare professional for accurate information"
            ],
            "when_to_consult": "Always seek professional medical advice for health concerns",
            "conversational_tone": [
                "Medical information can be nuanced and specific.",
                "Professional guidance is crucial for understanding health conditions."
            ],
            "database_update": False
        }
        
        # Fetch UMLS data
        umls_data = fetch_umls_data(processed_term)
        umls_definition = umls_data.get('definitions', [''])[0]
        
        # Generate explanation using Gemini
        improved_explanation = query_gemini(processed_term, umls_definition)
        
        # Safely extract medical details
        medical_details = improved_explanation.get('medical_details', default_response)
        
        # Construct medical explanation
        medical_explanation = {
            "term": processed_term,
            "simple_explanation": medical_details.get('simple_explanation', default_response['simple_explanation']),
            "signs": medical_details.get('signs_to_notice', default_response['signs']),
            "care_tips": medical_details.get('care_advice', default_response['care_tips']),
            "when_to_consult": medical_details.get('doctor_consultation_advice', default_response['when_to_consult']),
            "conversational_tone": default_response['conversational_tone'],
          
        }
        
        # Prepare data for database insertion
        current_time = datetime.now().isoformat()
        table_id = "useful-melody-444213-m6.tbird_resources.dbtable"
        
        # Prepare rows for upsert with comprehensive details
        rows_to_upsert = [
            {
                "Term": processed_term,
                "Simplified_Explanation": str(medical_explanation.get('simple_explanation', ''))[:500],
                "Patient_Friendly_Explanation": str(medical_explanation.get('simple_explanation', ''))[:500],
                "Care_Tips": '|'.join(str(tip) for tip in medical_explanation.get('care_tips', []))[:500],
                "Symptoms": '|'.join(str(sign) for sign in medical_explanation.get('signs', []))[:500],
                "When_To_Consult_Doctor": str(medical_explanation.get('when_to_consult', ''))[:500],
                "Date_Added": current_time,
                "Last_Queried": current_time,
                "API_Tokens_Used": int(improved_explanation.get('total_tokens', 0)),
                "UMLS_Definition": str(umls_definition)[:500]  # Include UMLS definition
            }
        ]
        
        # Perform database upsert operation
        try:
            merge_query = f"""
            MERGE `{table_id}` T
            USING UNNEST(@rows) S
            ON T.Term = S.Term
            WHEN MATCHED THEN
                UPDATE SET 
                    Simplified_Explanation = S.Simplified_Explanation,
                    Patient_Friendly_Explanation = S.Patient_Friendly_Explanation,
                    Care_Tips = S.Care_Tips,
                    Symptoms = S.Symptoms,
                    When_To_Consult_Doctor = S.When_To_Consult_Doctor,
                    Last_Queried = S.Last_Queried,
                    API_Tokens_Used = S.API_Tokens_Used,
                    UMLS_Definition = S.UMLS_Definition
            WHEN NOT MATCHED THEN
                INSERT (
                    Term, 
                    Simplified_Explanation, 
                    Patient_Friendly_Explanation, 
                    Care_Tips, 
                    Symptoms, 
                    When_To_Consult_Doctor,
                    Date_Added,
                    Last_Queried,
                    API_Tokens_Used,
                    UMLS_Definition
                )
                VALUES (
                    S.Term, 
                    S.Simplified_Explanation, 
                    S.Patient_Friendly_Explanation, 
                    S.Care_Tips, 
                    S.Symptoms, 
                    S.When_To_Consult_Doctor,
                    S.Date_Added,
                    S.Last_Queried,
                    S.API_Tokens_Used,
                    S.UMLS_Definition
                )
            """
            
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("rows", "STRUCT", rows_to_upsert)
                ]
            )
            
            # Execute the query
            query_job = bq_client.query(merge_query, job_config=job_config)
            query_job.result()  # Wait for the job to complete
            
            # Mark database update as successful
            medical_explanation['database_update'] = True
            
        except Exception as db_error:
            print(f"Database upsert error: {db_error}")
            # Log the error but don't stop the process
        
        return medical_explanation
    
    except Exception as e:
        # Log the error
        print(f"Error in get_medical_explanation: {e}")
        
        # Return default response in case of any error
        return default_response
 

@app.get("/performance-metrics")
def get_performance_metrics(term: str):
    try:
        # Measure start time
        start_time = time.time()
        
        # Fetch UMLS definition for term
        umls_data = fetch_umls_data(term)
        umls_definition = umls_data['definitions'][0] if umls_data['definitions'] else ''

        # Query Gemini and measure response time
        gemini_response = query_gemini(term, umls_definition)
        end_time = time.time()

        # Extract token count (Gemini's response includes token usage)
        total_tokens_used = gemini_response.get('total_tokens', 0)

        # Calculate response time
        response_time = end_time - start_time

        return {
            "term": term,
            "response_time_seconds": round(response_time, 2),
            "total_tokens_used": total_tokens_used,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        print(f"Error in /performance-metrics for term '{term}': {e}")
        return {
            "status": "error",
            "message": "Could not retrieve performance metrics.",
            "details": str(e)
        }


    # Run the query
    query_job = bq_client.query(merge_query, job_config=job_config)
    query_job.result()  # Wait for the job to complete

