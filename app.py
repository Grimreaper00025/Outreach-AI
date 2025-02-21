import os
import asyncio
import base64
import csv
import json
import random
import time
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.utils import secure_filename
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from serpapi import GoogleSearch
from config import SERP_API_KEY, MIRA_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from flask_dance.contrib.google import make_google_blueprint, google
from urllib.parse import urlparse, parse_qs
from oauthlib.oauth2 import TokenExpiredError
from mira_sdk import MiraClient, Flow, File, Reader
from pdf2image import convert_from_path
import pytesseract
from PIL import Image
# Allow insecure transport for development only.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
from mira_sdk import MiraClient, Flow, File, Reader, ComposioConfig

client = MiraClient(config={"API_KEY": "sb-f5d7f4055a7cabad7b8d5a848453c8f8"})

app = Flask(__name__)
app.secret_key = "replace_with_your_secret_key"

# Register the Google OAuth blueprint (using default redirect URI)
google_bp = make_google_blueprint(
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    scope=[
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid"
    ],
)
app.register_blueprint(google_bp, url_prefix="/login")

# Inject 'google' into all templates
@app.context_processor
def inject_google():
    return dict(google=google)

# Configure upload folder for temporary CV storage
UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Function to extract scholar ID from a Google Scholar URL
def extract_scholar_id(url):
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    return query_params.get("user", [None])[0]

#cv converter

# --- New Function: Convert PDF to Text using PyPDF2 ---
def convert_pdf_to_text(file_path):
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        if text.strip():
            return text
        else:
            raise Exception("Empty text extracted with PyPDF2.")
    except Exception as e:
        app.logger.error(f"PyPDF2 extraction failed: {e}. Attempting OCR...")
        try:
            images = convert_from_path(file_path)
            ocr_text = ""
            for image in images:
                ocr_text += pytesseract.image_to_string(image) + "\n"
            if ocr_text.strip():
                return ocr_text
            else:
                raise Exception("Empty text extracted via OCR.")
        except Exception as ex:
            app.logger.error(f"OCR extraction failed: {ex}")
            return f"Random CV content {random.randint(1000,9999)}"


# New helper: Parse CSV file to extract scholar links (assumes one link per row)
def parse_csv_for_links(csv_file_path):
    links = []
    with open(csv_file_path, newline='', encoding='utf-8') as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            for cell in row:
                cell = cell.strip()
                if cell.startswith("https://scholar.google.com/citations"):
                    links.append(cell)
    return links

def fetch_professor_data(scholar_id):
    params = {
        "engine": "google_scholar_author",
        "author_id": scholar_id,
        "api_key": SERP_API_KEY
    }
    search = GoogleSearch(params)
    results = search.get_dict()
    
    author = results.get("author", {})
    professor_email = author.get("email", "Not Available")
    interests = [item["title"] for item in author.get("interests", [])]
    name = author.get("name", "Professor")
    affiliation = author.get("affiliations", "Not Available")
    articles = results.get("articles", [])
    work_highlight = articles[0]["title"] if articles else "your recent work"
    
    return {
        "name": name,
        "email": professor_email,
        "affiliation": affiliation,
        "interests": interests,
        "work_highlight": work_highlight
    }

def extract_email_with_mira(page_text):
    # Use your Mira flow for email extraction (YAML file: email.yaml)
    flow = Flow(source="./email.yaml")
    input_dict = {"input": page_text}
    response = client.flow.test(flow, input_dict)
    # Expecting the response JSON to have the key "result" with the email.
    email_extracted = response.get("result", "No email found")
    return email_extracted

def get_email_with_selenium(professor_name, affiliation):
    query = f"{professor_name} {affiliation} email"
    params = {
        "engine": "google",
        "q": query,
        "api_key": SERP_API_KEY
    }
    search = GoogleSearch(params)
    results = search.get_dict()
    organic_results = results["organic_results"]
    page_text = organic_results[0].get("snippet", "")
    extracted_email = extract_email_with_mira(page_text)
    return extracted_email

# Simulated function to extract primary research domain from user's CV using Mira LLM
def extract_user_info(cv_text):
    try:
        flow = Flow(source="./extract_user_info.yaml")
        input_dict = {"input": cv_text}
        response = client.flow.test(flow, input_dict)
        # Expecting response to contain keys like "skills" and "interests"
        return response
    except Exception as e:
        app.logger.error(f"User info extraction error: {e}")
        return {"skills": [], "interests": []}
    
def process_cv(cv_text, file_path, filename):
    if filename.lower().endswith(".pdf"):
        cv_text = convert_pdf_to_text(file_path)
    else:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            cv_text = f.read()
    # Extract user's info from the CV via Mira.
    user_info = extract_user_info(cv_text)
    # Ensure we preserve the original text.
    user_info["text"] = cv_text
    return user_info

# email 
async def generate_email_template(professor_data, cv_data, user_email,user_name,user_institution):
    # Create a unique timestamp to ensure the subject is unique
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    
    # Build professor details string (input1)
    prof_details = (
        f"Name: {professor_data['name']}; "
        f"Email: {professor_data['email']}; "
        f"Interests: {', '.join(professor_data['interests'])}; "
        f"Work Highlight: {professor_data['work_highlight']}"
    )
    print(cv_data)
    # Build CV info string (input2) using the original CV text and the extracted skills/interests.
    cv_info = (
        f"User Name: {user_name}; Institution: {user_institution} ; User email: {user_email}\n\n"
    )
    # Build additional context (input3)
    additional_context = (
        "Generate a professional academic cold outreach email that meets the following requirements:\n"
        "- Describe the user's relevant background based on the CV.\n"
        "- Explain why the user is interested in the professor's research (referring to the professor's interests and work highlight).\n"
        "- Include a clear call-to-action for potential collaboration.\n"
        "Modify the language as needed so the email is engaging and professional."
    )
    flow = Flow(source = "./mira.yaml")
    # Prepare the input dictionary for the Mira flow.
    input_dict = {
        "input1": prof_details,
        "input2": cv_data,
        "input3": additional_context
    }

    response = client.flow.test(flow, input_dict)
    email_subject = "Exploring Collaboration Opportunities"
    email_body = response.get("result", "No email generated.")
    return email_subject, email_body


def send_email_gmail(user_email, professor_email, subject, body):
    message = MIMEMultipart()
    message['to'] = professor_email
    message['from'] = user_email
    message['subject'] = subject
    message.attach(MIMEText(body, 'plain'))
    
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    message_body = {'raw': raw_message}
    
    token = google.token
    creds = Credentials(
        token=token['access_token'],
        refresh_token=token.get('refresh_token'),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/gmail.send"]
    )
    service = build('gmail', 'v1', credentials=creds)
    
    try:
        service.users().messages().send(userId='me', body=message_body).execute()
        return True
    except Exception as e:
        app.logger.error(f"Gmail API error: {str(e)}")
        return False


@app.route("/", methods=["GET"])
def index():
    try:
        # Try to fetch user info from Google
        user_info = google.get('/oauth2/v2/userinfo').json()
    except TokenExpiredError:
        # If the token is expired, clear it and force re-authentication
        session.pop("google_oauth_token", None)
        flash("Session expired. Please sign in again.")
        return redirect(url_for("google.login"))
    # Pass user_info to the template so it doesn't need to fetch it itself.
    return render_template("index.html", user_info=user_info)


@app.route("/fetch", methods=["POST"])

def fetch():
    user_email = request.form.get("user_email")
    manual_links = request.form.getlist("scholar_links[]")
    cv_file = request.files.get("cv")
    csv_file = request.files.get("csv_file")
    user_name = request.form.get("user_name")             
    user_institution = request.form.get("user_institution")
    
    if not user_email or not cv_file or (not manual_links and not csv_file):
        flash("User email, CV, and at least one professor link (manual or CSV) are required!")
        return redirect(url_for("index"))
    
    # Save CV file temporarily
    filename = secure_filename(cv_file.filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    cv_file.save(file_path)

    # Convert PDF if needed; otherwise read text normally.
    if filename.lower().endswith(".pdf"):
        cv_text = convert_pdf_to_text(file_path)
    else:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            cv_text = f.read()
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        cv_text = f.read()
    cv_data = process_cv(cv_text, file_path, filename)
    cv_data = cv_data.get("result")
    # Get professor links from manual input
    professor_links = [link.strip() for link in manual_links if link.strip()]
    
    # If CSV file is provided, parse it and add links
    if csv_file and csv_file.filename:
        csv_filename = secure_filename(csv_file.filename)
        csv_path = os.path.join(app.config["UPLOAD_FOLDER"], csv_filename)
        csv_file.save(csv_path)
        csv_links = parse_csv_for_links(csv_path)
        professor_links.extend(csv_links)
        os.remove(csv_path)
    
    # Remove duplicate links if any
    professor_links = list(set(professor_links))
    
    async def generate_all_emails():
        results = []
        for link in professor_links:
            scholar_id = extract_scholar_id(link)
            if not scholar_id:
                continue
            professor_data = fetch_professor_data(scholar_id)
            extracted_email = get_email_with_selenium(professor_data["name"], professor_data["affiliation"])
            professor_data["email"] = extracted_email
            email_subject, email_body = await generate_email_template(professor_data, cv_data, user_email,user_name,user_institution)
            results.append({
                "professor_data": professor_data,
                "email_subject": email_subject,
                "email_body": email_body
            })
        return results

    all_emails = asyncio.run(generate_all_emails())
    os.remove(file_path)
    
    if not all_emails:
        flash("No valid professor links were provided.")
        return redirect(url_for("index"))
    sample_email = random.choice(all_emails)
    # Pass the raw object so the template uses |tojson once.
    return render_template("email_preview.html",
                           sample_email=sample_email,
                           emails_json=all_emails,
                           user_email=user_email)

@app.route("/send", methods=["POST"])
def send():
    user_email = request.form.get("user_email")
    emails_json = request.form.get("emails_json")
    if not user_email or not emails_json:
        flash("Missing email data.")
        return redirect(url_for("index"))
    all_emails = json.loads(emails_json)
    
    all_sent = True
    for email in all_emails:
        # email["professor_data"]["email"]
        professor_email = "inficos0520@gmail.com"
        subject = email["email_subject"]
        body = email["email_body"]
        if not send_email_gmail(user_email, professor_email, subject, body):
            all_sent = False
    
    if all_sent:
        flash("All emails sent successfully!")
    else:
        flash("Some emails could not be sent. Check logs for details.")
    
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)
