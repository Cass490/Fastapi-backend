from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.cloud import bigquery
from google.oauth2 import service_account
import os
from dotenv import load_dotenv
from datetime import datetime
import time
from gemini import query_gemini, validate_response_coverage, fetch_umls_data  # Your existing Gemini integration
import spacy
from typing import Optional
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
        # Load the appropriate spaCy model
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
@app.get("/chatbot-response")
def get_chatbot_medical_explanation(term: str):
    try:
        # Fetch existing or generate new explanation
        umls_data = fetch_umls_data(term)
        umls_definition = umls_data['definitions'][0] if umls_data['definitions'] else ''
        
        # Query Gemini for chatbot-style response
        improved_explanation = query_gemini(term, umls_definition)
        
        # Validate and extract medical details
        medical_details = improved_explanation.get('medical_details', {})
        
        # Construct chatbot-friendly response
        chatbot_response = {
            "initial_greeting": f"Let me help you understand {term}.",
            "simple_explanation": medical_details.get('simple_explanation', 'I could not find a clear explanation.'),
            "signs": medical_details.get('signs_to_notice', []),
            "care_tips": medical_details.get('care_advice', []),
            "when_to_consult": medical_details.get('doctor_consultation_advice', ''),
            "conversational_tone": [
                "Always consult with a healthcare professional for personalized advice.",
                "This information is meant to provide general understanding."
            ]
        }
        
        return chatbot_response
    
    except Exception as e:
        return {
            "error": "Could not generate explanation",
            "details": str(e)
        }

@app.post("/add-medical-term")
def add_medical_term(medical_term: MedicalTermInput):
    """
    Endpoint for adding a new medical term to the database.
    If no explanation is provided, it generates one using Gemini.
    """
    try:
        # Process the term
        processed_term = extract_medical_term(medical_term.term)
        
        # If no simplified explanation is provided, generate one
        if not medical_term.simplified_explanation:
            # Fetch UMLS data
            umls_data = fetch_umls_data(processed_term)
            umls_definition = umls_data['definitions'][0] if umls_data['definitions'] else ''
            
            # Generate explanation using Gemini
            improved_explanation = query_gemini(processed_term, umls_definition)
            medical_details = improved_explanation.get('medical_details', {})
        else:
            # Use provided explanation
            medical_details = {
                'simple_explanation': medical_term.simplified_explanation
            }
        
        # Prepare data for database insertion
        current_time = datetime.now().isoformat()
        table_id = "useful-melody-444213-m6.tbird_resources.dbtable"
        
        # Calculate concept coverage
        concept_coverage = validate_response_coverage(
            fetch_umls_data(processed_term).get('definitions', [''])[0], 
            medical_details
        )
        
        rows_to_upsert = [{
            "Term": processed_term,
            "Simplified_Explanation": medical_details.get('simple_explanation', ''),
            "Patient_Friendly_Explanation": medical_details.get('simple_explanation', ''),
            "Care_Tips": medical_term.additional_details.get('care_tips', '') if medical_term.additional_details else '',
            "Symptoms": medical_term.additional_details.get('symptoms', '') if medical_term.additional_details else '',
            "Date_Added": current_time,
            "Last_Queried": current_time,
            "Average_Concept_Coverage": concept_coverage
        }]
        
        # Perform upsert operation
        try:
            upsert_medical_term(bq_client, table_id, rows_to_upsert)
            db_status = "Success"
        except Exception as db_error:
            db_status = f"Database Error: {str(db_error)}"
            # Log the error for further investigation
            print(f"Database upsert error: {db_error}")
        
        return {
            "status": "success",
            "term": processed_term,
            "database_status": db_status,
            "explanation": medical_details.get('simple_explanation', ''),
            "concept_coverage": concept_coverage
        }
    
    except Exception as e:
        # Log the error for debugging
        print(f"Error in add_medical_term: {e}")
        
        return {
            "status": "error",
            "details": str(e)
        }

@app.post("/add-term")
def add_medical_term(data: MedicalTermInput):
    try:
        # If no simplified explanation provided, use Gemini to generate one
        if not data.simplified_explanation:
            gemini_explanation = query_gemini(data.term, None)
            
            # Validate the generated explanation
            if 'error' in gemini_explanation:
                return {
                    "status": "error",
                    "message": "Could not generate explanation for the term"
                }
            
            medical_details = gemini_explanation.get('medical_details', {})
            
            # Prepare data for insertion
            rows_to_insert = [{
                "Term": data.term,
                "Simplified_Explanation": medical_details.get('simple_explanation', ''),
                "Patient_Friendly_Explanation": medical_details.get('simple_explanation', ''),
                "Care_Tips": '|'.join(medical_details.get('care_advice', [])),
                "Symptoms": '|'.join(medical_details.get('signs_to_notice', [])),
                "When_To_Consult_Doctor": medical_details.get('doctor_consultation_advice', ''),
                "Date_Added": datetime.now().isoformat(),
                "Average_Concept_Coverage": validate_response_coverage(
                    fetch_umls_data(data.term).get('definitions', [''])[0], 
                    medical_details
                )
            }]
            
            # Perform insertion
            table_id = "useful-melody-444213-m6.tbird_resources.dbtable"
            errors = bq_client.insert_rows_json(table_id, rows_to_insert)
            
            if errors:
                return {
                    "status": "partial_error",
                    "message": "Term added with some issues",
                    "errors": errors
                }
            
            return {
                "status": "success",
                "message": "Term added successfully",
                "term": data.term
            }
    
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

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


# Existing function to support other operations
def upsert_medical_term(bq_client, table_id, rows):
    """
    Perform an upsert operation using MERGE statement
    """
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
            Average_Concept_Coverage = S.Average_Concept_Coverage,
            API_Tokens_Used = S.API_Tokens_Used
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
            Average_Concept_Coverage,
            API_Tokens_Used
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
            S.Average_Concept_Coverage,
            S.API_Tokens_Used
        )
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("rows", "STRUCT", rows)
        ]
    )
    
    # Run the query
    query_job = bq_client.query(merge_query, job_config=job_config)
    query_job.result()  # Wait for the job to complete

