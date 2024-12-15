import os
import requests
import json
from dotenv import load_dotenv
import spacy
from google.cloud import bigquery
from google.oauth2 import service_account
from datetime import datetime
import re
import json

# Load environment variables
load_dotenv('key.env')

# Retrieve API Keys
UMLS_API_KEY = os.getenv("UMLS_API_KEY")

# Initialize BigQuery Client
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')
bq_credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/bigquery"]
)

bq_client = bigquery.Client(
    credentials=bq_credentials, 
    project='useful-melody-444213-m6'  # Replace with your project ID
)

# Load spaCy model (medical model if available)
try:
    nlp = spacy.load('en_core_sci_md')  # Use SciSpaCy model
except OSError:
    nlp = spacy.load('en_core_web_sm')  # Fallback to general model

def fetch_umls_data(term):
    """
    Fetch UMLS definitions for a medical term.
    """
    search_url = "https://uts-ws.nlm.nih.gov/rest/search/current"
    params = {"string": term, "apiKey": UMLS_API_KEY}

    try:
        search_response = requests.get(search_url, params=params)
        search_response.raise_for_status()
        results = search_response.json().get("result", {}).get("results", [])

        if not results:
            return {"term": term, "definitions": []}

        # Retrieve first CUI and fetch definitions
        cui = results[0]['ui']
        def_url = f"https://uts-ws.nlm.nih.gov/rest/content/current/CUI/{cui}/definitions"
        def_response = requests.get(def_url, params={"apiKey": UMLS_API_KEY})
        def_response.raise_for_status()

        definitions = [d['value'] for d in def_response.json().get("result", [])]
        return {"term": term, "cui": cui, "definitions": definitions}

    except Exception as e:
        print(f"UMLS API Error: {e}")
        return {"term": term, "definitions": []}

def extract_key_concepts(text):
    """
    Extract key concepts from text using spaCy.
    """
    if not text:
        return []
    doc = nlp(text)
    return list(set(token.lemma_.lower() for token in doc if token.pos_ in ["NOUN", "PROPN"] and len(token.text) > 2))
    
#to check how much of the UMLS clinically accurate definition's content is reflected in Gemini's structured output
def validate_response_coverage(umls_definition, gemini_response, threshold=0.4):
    """
    Validate that Gemini's response aligns with UMLS concepts.
    """
    if not umls_definition:
        print("UMLS Definition is empty.")
        return 1.0  # If no UMLS definition, assume full coverage.

    umls_concepts = extract_key_concepts(umls_definition)
    response_text = ' '.join([
        gemini_response.get('simple_explanation', ''),
        ' '.join(gemini_response.get('signs_to_notice', [])),
        ' '.join(gemini_response.get('care_advice', [])),
        gemini_response.get('doctor_consultation_advice', '')
    ])
    response_concepts = extract_key_concepts(response_text)

    print(f"UMLS Concepts: {umls_concepts}")
    print(f"Response Concepts: {response_concepts}")

    matching_concepts = set(umls_concepts) & set(response_concepts)
    coverage = len(matching_concepts) / len(umls_concepts) if umls_concepts else 0.0

    print(f"Matching Concepts: {matching_concepts}")
    print(f"Concept Coverage: {coverage * 100:.2f}%")
    return coverage

def parse_gemini_response(response_text):
    """
    Parse Gemini's response into structured format with improved flexibility.
    """
    print("Raw Response Text:", response_text)  # Debug print

    # Clean and standardize the response text
    lines = response_text.strip().split("\n")
    sections = {
        'simple_explanation': '',
        'signs_to_notice': [],
        'care_advice': [],
        'doctor_consultation_advice': ''
    }

    current_section = None

    for line in lines:
        line = line.strip()

        # Match headers regardless of formatting
        if re.match(r"^SIMPLE EXPLANATION[:\s]*", line, re.IGNORECASE):
            current_section = 'simple_explanation'
            sections[current_section] = re.sub(r"^SIMPLE EXPLANATION[:\s]*", "", line, flags=re.IGNORECASE).strip()
        elif re.match(r"^SIGNS TO NOTICE[:\s]*", line, re.IGNORECASE):
            current_section = 'signs_to_notice'
        elif re.match(r"^CARE ADVICE[:\s]*", line, re.IGNORECASE):
            current_section = 'care_advice'
        elif re.match(r"^DOCTOR CONSULTATION[:\s]*", line, re.IGNORECASE):
            current_section = 'doctor_consultation_advice'
            sections[current_section] = re.sub(r"^DOCTOR CONSULTATION[:\s]*", "", line, flags=re.IGNORECASE).strip()

        # Append bullet points or relevant lines to current section
        elif current_section == 'signs_to_notice' and line.startswith("•"):
            sections['signs_to_notice'].append(line.replace("•", "").strip())
        elif current_section == 'care_advice' and line.startswith("•"):
            sections['care_advice'].append(line.replace("•", "").strip())
        elif current_section == 'simple_explanation' and current_section and not line.startswith("**"):
            sections['simple_explanation'] += " " + line.strip()

    print("Parsed Sections:", sections)  # Debug print
    return sections
      
def query_gemini(term, simplified_explanation, max_attempts=3):
    """
    Generate validated medical explanations using Gemini API.
    """
    # Fetch UMLS data
    umls_data = fetch_umls_data(term)
    umls_definition = umls_data['definitions'][0] if umls_data['definitions'] else ''

    #If coverage is satisfactory and matches UMLS definition the response is returned; otherwise, the loop tries to regenerate it
    for attempt in range(max_attempts):
        try:
            # Craft prompt for Gemini
            prompt = f"""
            Medical Term: {term}
            UMLS Definition: {umls_definition}
            Simplified Explanation: {simplified_explanation}

            Generate a structured response with the following sections:

            SIMPLE EXPLANATION: [One clear, non-technical sentence about the medical term]
            SIGNS TO NOTICE:
            • [First sign to look out for]
            • [Second sign to notice]
            • [Third sign to be aware of]
            CARE ADVICE:
            • [First practical care tip]
            • [Second helpful care suggestion]
            • [Third self-care recommendation]
            DOCTOR CONSULTATION: [One sentence advising when to seek medical help]

            Ensure clinical accuracy and integrate UMLS concepts.
            """

            # Generate response
            response = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent",
                params={"key": os.getenv('GEMINI_API_KEY')},
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]}
            )
            response.raise_for_status()

            # Extract text from response
            gemini_response_text = response.json()['candidates'][0]['content']['parts'][0]['text']
            print("Raw Gemini Response:", gemini_response_text)  # Debug print

            # Parse response
            parsed_response = parse_gemini_response(gemini_response_text)
            print("Parsed Response:", parsed_response)  # Debug print
            # After generating the response
            print("Raw Gemini Response Text:", gemini_response_text)
            print("Response JSON:", response.json())
            # Calculate tokens
            total_tokens = len(prompt.split())
            response_tokens = len(gemini_response_text.split())
            api_tokens_used = total_tokens + response_tokens

            # Validate response coverage
            if validate_response_coverage(umls_definition, parsed_response):
                return {
                    "term": term,
                    "medical_details": parsed_response,
                    "umls_definition": umls_definition,
                    "total_tokens": api_tokens_used
                }
            else:
                print("Response coverage validation failed")

        except Exception as e:
            print(f"Gemini API Error on attempt {attempt + 1}: {e}")
            # If possible, print out the full response to see what's happening
            try:
                print("Full API Response:", response.json())
            except:
                pass

    # If all attempts fail
    return {
        "term": term,
        "medical_details": {
            "simple_explanation": f"Could not generate an explanation for {term}",
        },
        "error": "Could not generate an accurate medical explanation",
        "fallback_explanation": simplified_explanation
    }
